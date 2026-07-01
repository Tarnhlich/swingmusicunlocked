from gettext import ngettext
from io import BytesIO
import random
import time
from urllib.parse import quote
import requests
from PIL import Image

from swingmusic.db.userdata import MixTable, SimilarArtistTable
from swingmusic.models.artist import Artist
from swingmusic.models.mix import Mix
from swingmusic.models.track import Track
from swingmusic.plugins import Plugin, plugin_method
from swingmusic.settings import Paths
from swingmusic.store.albums import AlbumStore
from swingmusic.store.artists import ArtistStore
from swingmusic.store.tracks import TrackStore
from swingmusic.utils.dates import get_date_range, get_duration_ago
from swingmusic.utils.hashing import create_hash
from swingmusic.utils.mixes import balance_mix
from swingmusic.utils.stats import get_artists_in_period


class MixAlreadyExists(Exception):
    """
    Raised when a mix with the same sourcehash already exists.
    """

    pass


class MixesPlugin(Plugin):
    MAX_TRACKS_TO_FETCH = 5
    MIN_TRACK_MIX_LENGTH = 15
    MIN_ARTISTS_PER_MIX = 4
    MIX_TRACKS_LENGTH = 40
    MAX_TRACKS_PER_ARTIST = 3
    LOCAL_RELATED_LIMIT = 24
    MAX_SEED_ARTIST_TRACKS = 1
    _current_seed_primary_artists: set[str] = set()

    MIN_DAY_LISTEN_DURATION = 3 * 60  # 3 minutes
    MIN_WEEK_LISTEN_DURATION = 10 * 60  # 10 minutes
    MIN_MONTH_LISTEN_DURATION = 20 * 60  # 20 minutes

    def __init__(self):
        super().__init__("mixes", "Mixes")
        self.server = "https://smcloud.mungaist.com"
        self.set_active(True)

    @plugin_method
    def get_track_mix_data(self, tracks: list[Track], with_help: bool = False):
        """
        Given a list of tracks, creates a mix using locally available library
        metadata and similar-artist data.

        :param with_help: Whether to include the help flag in the query.
            The flag slightly relaxes the album overlap penalty.
        """
        trackmatches, albums, artists = self.get_local_track_mix_data(
            tracks, with_help=with_help
        )

        if len(trackmatches) < self.MIN_TRACK_MIX_LENGTH:
            filler_tracks = self.fallback_create_artist_mix(
                similar_artists=artists,
                similar_albums=albums,
                omit_trackhashes={t.weakhash for t in trackmatches},
            )
            trackmatches.extend(filler_tracks)

        trackmatches = balance_mix(trackmatches)
        return trackmatches, albums, artists

    @staticmethod
    def _get_primary_artisthash(track: Track) -> str:
        if not track.artists:
            return ""

        return track.artists[0].get("artisthash", "")

    @staticmethod
    def _coerce_similarity_value(entry, key: str, default=None):
        if isinstance(entry, dict):
            return entry.get(key, default)

        return getattr(entry, key, default)

    @staticmethod
    def _get_track_decade(track: Track) -> int | None:
        year = getattr(track, "date", 0) or 0

        if year < 1000:
            return None

        return int(year) // 10 * 10

    @classmethod
    def _get_seed_profile(cls, tracks: list[Track]) -> dict[str, set[str] | set[int]]:
        primary_artists = set()
        all_artists = set()
        album_artists = set()
        genres = set()
        decades = set()

        for track in tracks:
            primary_artisthash = cls._get_primary_artisthash(track)
            if primary_artisthash:
                primary_artists.add(primary_artisthash)

            all_artists.update(track.artisthashes)
            album_artists.update(a["artisthash"] for a in track.albumartists)
            genres.update(track.genrehashes)

            decade = cls._get_track_decade(track)
            if decade is not None:
                decades.add(decade)

        return {
            "primary_artists": primary_artists,
            "all_artists": all_artists,
            "album_artists": album_artists,
            "genres": genres,
            "decades": decades,
        }

    @staticmethod
    def _score_decade_match(candidate_decade: int | None, seed_decades: set[int]) -> float:
        if candidate_decade is None or not seed_decades:
            return 0.0

        distances = [abs(candidate_decade - decade) for decade in seed_decades]
        nearest = min(distances)

        if nearest == 0:
            return 40.0

        if nearest == 10:
            return 20.0

        if nearest == 20:
            return 8.0

        return 0.0

    @staticmethod
    def _score_novelty(candidate: Track) -> float:
        playcount = getattr(candidate, "playcount", 0) or 0
        playduration = getattr(candidate, "playduration", 0) or 0

        novelty_bonus = max(0.0, 18.0 - min(playcount, 18))
        novelty_bonus += max(0.0, 14.0 - min(playduration / 180, 14))

        popularity_penalty = min(playcount / 2.5, 18.0)
        popularity_penalty += min(playduration / 900, 16.0)

        return novelty_bonus - popularity_penalty

    @classmethod
    def _get_similar_artist_scores(
        cls, seed_artisthashes: set[str]
    ) -> dict[str, float]:
        scores: dict[str, float] = {}

        for artisthash in seed_artisthashes:
            similar = SimilarArtistTable.get_by_hash(artisthash)
            if similar is None or not similar.similar_artists:
                continue

            for index, entry in enumerate(similar.similar_artists):
                similar_hash = cls._coerce_similarity_value(entry, "artisthash", "")
                if not similar_hash or similar_hash in seed_artisthashes:
                    continue

                if similar_hash not in ArtistStore.artistmap:
                    continue

                weight = cls._coerce_similarity_value(entry, "weight", None)

                try:
                    score = float(weight) if weight is not None else 0.0
                except (TypeError, ValueError):
                    score = 0.0

                if score <= 0:
                    score = max(1.0, 100.0 - index * 5)

                scores[similar_hash] = max(scores.get(similar_hash, 0.0), score)

        return scores

    @classmethod
    def _score_local_candidate(
        cls,
        candidate: Track,
        seed_trackhashes: set[str],
        seed_weakhashes: set[str],
        seed_albumhashes: set[str],
        seed_profile: dict[str, set[str] | set[int]],
        similar_artist_scores: dict[str, float],
        with_help: bool,
    ) -> float:
        if (
            candidate.trackhash in seed_trackhashes
            or candidate.weakhash in seed_weakhashes
        ):
            return -1

        primary_artisthash = cls._get_primary_artisthash(candidate)
        candidate_artisthashes = set(candidate.artisthashes)
        candidate_genrehashes = set(candidate.genrehashes)
        candidate_album_artists = {a["artisthash"] for a in candidate.albumartists}
        candidate_decade = cls._get_track_decade(candidate)

        seed_primary_artists: set[str] = seed_profile["primary_artists"]  # type: ignore[assignment]
        seed_all_artists: set[str] = seed_profile["all_artists"]  # type: ignore[assignment]
        seed_album_artists: set[str] = seed_profile["album_artists"]  # type: ignore[assignment]
        seed_genres: set[str] = seed_profile["genres"]  # type: ignore[assignment]
        seed_decades: set[int] = seed_profile["decades"]  # type: ignore[assignment]

        score = 0.0
        theme_signals = 0

        if primary_artisthash in similar_artist_scores:
            score += 80 + min(similar_artist_scores[primary_artisthash], 70)
            theme_signals += 1

        overlapping_artists = candidate_artisthashes.intersection(seed_all_artists)
        if overlapping_artists:
            if primary_artisthash in seed_primary_artists:
                score += 10 if with_help else -15
            else:
                score += len(overlapping_artists) * 18
                theme_signals += 1

        overlapping_album_artists = candidate_album_artists.intersection(seed_album_artists)
        if overlapping_album_artists:
            score += len(overlapping_album_artists) * 14
            theme_signals += 1

        overlapping_genres = candidate_genrehashes.intersection(seed_genres)
        if overlapping_genres:
            score += min(len(overlapping_genres), 3) * 55
            theme_signals += 1

        if candidate.albumhash in seed_albumhashes:
            score += 20 if with_help else 5
            theme_signals += 1

        decade_score = cls._score_decade_match(candidate_decade, seed_decades)
        score += decade_score
        if decade_score > 0:
            theme_signals += 1

        score += cls._score_novelty(candidate)
        score += min(candidate.bitrate / 96, 4)

        if primary_artisthash in seed_primary_artists and not with_help:
            score -= 20

        if theme_signals == 0:
            return -1

        if theme_signals == 1:
            score -= 20

        return score

    @classmethod
    def _dedupe_and_limit_tracks(cls, ranked_tracks: list[Track]) -> list[Track]:
        results: list[Track] = []
        seen_weakhashes: set[str] = set()
        per_artist_count: dict[str, int] = {}

        for track in ranked_tracks:
            primary_artisthash = cls._get_primary_artisthash(track)

            if track.weakhash in seen_weakhashes:
                continue

            max_tracks = cls.MAX_TRACKS_PER_ARTIST
            if primary_artisthash in cls._current_seed_primary_artists:
                max_tracks = cls.MAX_SEED_ARTIST_TRACKS

            if per_artist_count.get(primary_artisthash, 0) >= max_tracks:
                continue

            results.append(track)
            seen_weakhashes.add(track.weakhash)
            per_artist_count[primary_artisthash] = (
                per_artist_count.get(primary_artisthash, 0) + 1
            )

            if len(results) >= cls.MIX_TRACKS_LENGTH * 2:
                break

        return results

    @classmethod
    def get_local_track_mix_data(
        cls, tracks: list[Track], with_help: bool = False
    ) -> tuple[list[Track], list[str], list[str]]:
        seed_trackhashes = {track.trackhash for track in tracks}
        seed_weakhashes = {track.weakhash for track in tracks}
        seed_albumhashes = {track.albumhash for track in tracks}
        seed_profile = cls._get_seed_profile(tracks)
        seed_artisthashes: set[str] = seed_profile["all_artists"]  # type: ignore[assignment]

        similar_artist_scores = cls._get_similar_artist_scores(seed_artisthashes)
        cls._current_seed_primary_artists = set(seed_profile["primary_artists"])  # type: ignore[arg-type]

        ranked_candidates: list[tuple[float, Track]] = []

        for group in TrackStore.trackhashmap.values():
            candidate = group.get_best()
            score = cls._score_local_candidate(
                candidate,
                seed_trackhashes=seed_trackhashes,
                seed_weakhashes=seed_weakhashes,
                seed_albumhashes=seed_albumhashes,
                seed_profile=seed_profile,
                similar_artist_scores=similar_artist_scores,
                with_help=with_help,
            )

            if score <= 0:
                continue

            ranked_candidates.append((score, candidate))

        ranked_candidates.sort(
            key=lambda item: (
                -item[0],
                item[1].playcount,
                item[1].playduration,
                -item[1].bitrate,
            )
        )

        ranked_tracks = [track for _, track in ranked_candidates]
        ranked_tracks = cls._dedupe_and_limit_tracks(ranked_tracks)

        related_artists: list[str] = []
        for artisthash, _ in sorted(
            similar_artist_scores.items(), key=lambda item: item[1], reverse=True
        ):
            related_artists.append(artisthash)
            if len(related_artists) >= cls.LOCAL_RELATED_LIMIT:
                break

        for track in ranked_tracks:
            artisthash = cls._get_primary_artisthash(track)
            if not artisthash or artisthash in seed_artisthashes or artisthash in related_artists:
                continue

            related_artists.append(artisthash)
            if len(related_artists) >= cls.LOCAL_RELATED_LIMIT:
                break

        related_albums: list[str] = []
        for track in ranked_tracks:
            if track.albumhash in seed_albumhashes or track.albumhash in related_albums:
                continue

            related_albums.append(track.albumhash)
            if len(related_albums) >= cls.LOCAL_RELATED_LIMIT:
                break

        return ranked_tracks, related_albums, related_artists

    # @plugin_method
    # def get_artist_mix(self, artisthash: str):
    #     """
    #     Given an artisthash, creates an artist mix using the
    #     self.MAX_TRACKS_TO_FETCH most listened to tracks.

    #     Returns a tuple of the mix and the sourcehash.
    #     """
    #     artist = ArtistStore.artistmap[artisthash]
    #     tracks = TrackStore.get_tracks_by_trackhashes(artist.trackhashes)

    #     tracks = sorted(tracks, key=lambda x: x.playduration, reverse=True)
    #     sourcetracks = tracks[: self.MAX_TRACKS_TO_FETCH]
    #     sourcehash = create_hash(*[t.trackhash for t in sourcetracks])

    #     if MixTable.get_by_sourcehash(sourcehash):
    #         raise MixAlreadyExists()

    #     tracks, albums, artists = self.get_track_mix(tracks[: self.MAX_TRACKS_TO_FETCH])
    #     return (tracks, albums, artists, sourcehash)

    @plugin_method
    def create_artist_mixes(self, userid: int):
        """
        Creates artist mixes for a given userid.
        """
        mixes: list[Mix] = []
        indexed = set()

        today_start, today_end = get_date_range(duration="day")
        last_2_days_start = get_duration_ago("day", 2)
        last_7_days_start = get_duration_ago("week")
        last_1_month_start = get_duration_ago("month")

        artists = {
            "today": {
                "max": 4,
                "artists": get_artists_in_period(today_start, today_end, userid),
                "created": 0,
            },
            "last_2_days": {
                "max": 3,
                "artists": get_artists_in_period(
                    last_2_days_start, time.time(), userid
                ),
                "created": 0,
            },
            "last_7_days": {
                "max": 4,
                "artists": get_artists_in_period(
                    last_7_days_start, time.time(), userid
                ),
                "created": 0,
            },
            "last_1_month": {
                "max": 4,
                "artists": get_artists_in_period(
                    last_1_month_start, time.time(), userid
                ),
                "created": 0,
            },
        }

        # FIXME: Make sure that different artists don't generate the same mix

        for i, period in enumerate(artists.values()):
            # if previous period has less than its max
            # add the difference to this period's limit
            limit = period["max"]

            if i > 0:
                previous_period = artists[list(artists.keys())[i - 1]]
                if previous_period["created"] < previous_period["max"]:
                    limit += previous_period["max"] - previous_period["created"]

            for artist in period["artists"]:
                if period["created"] >= limit:
                    break

                if artist["artisthash"] in indexed:
                    continue

                # INFO: track['tracks'] is a dict of trackhashes and their counts
                # get the trackhashes sorted by count
                trackhashes = sorted(
                    artist["tracks"], key=lambda x: artist["tracks"][x], reverse=True
                )

                mix = self.create_artist_mix(
                    artist, trackhashes[: self.MAX_TRACKS_TO_FETCH], userid=userid
                )

                if mix:
                    mixes.append(mix)
                    indexed.add(artist["artisthash"])
                    period["created"] += 1

        return mixes

    @classmethod
    def get_mix_description(cls, tracks: list[Track], artishash: str):
        """
        Constructs a description for a mix by putting together the first n=4
        artists in the mix tracklist.
        """
        first_4_artists = []
        indexed = set()

        for track in tracks:
            if len(first_4_artists) < 4:
                if (
                    track.artists[0]["artisthash"] != artishash
                    and track.artists[0]["artisthash"] not in indexed
                ):
                    first_4_artists.append(track.artists[0])
                    indexed.add(track.artists[0]["artisthash"])

        if len(first_4_artists) == 4:
            return f"Featuring {', '.join(a['name'] for a in first_4_artists)} and more"

        if len(first_4_artists) > 0:
            return f"Featuring {', '.join(a['name'] for a in first_4_artists)}"

        return f"Featuring {tracks[0].artists[0]['name']}"

    def create_artist_mix(
        self, artist: dict[str, str], trackhashes: list[str], userid: int
    ):
        """
        Given an artist dict, creates an artist mix.
        """
        _artist = ArtistStore.artistmap.get(artist["artisthash"])

        if not _artist:
            return None

        tracks = TrackStore.get_tracks_by_trackhashes(trackhashes)
        # tracks = sorted(tracks, key=lambda x: x.playduration, reverse=True)
        # sourcetracks = tracks[: self.MAX_TRACKS_TO_FETCH]

        # INFO: Sort the trackhashes when creating the sourcehash
        sourcehash = create_hash(
            *sorted(trackhashes, key=lambda x: trackhashes.index(x))
        )

        db_mix = MixTable.get_by_sourcehash(sourcehash)
        if db_mix:
            return db_mix

        mix_tracks, albums, artists = self.get_track_mix_data(tracks)

        if len(mix_tracks) < self.MIN_TRACK_MIX_LENGTH:
            return None

        # INFO: Dump mixes with no variety
        if len(set(t.artisthashes[0] for t in mix_tracks)) < self.MIN_ARTISTS_PER_MIX:
            return None

        # try downloading artist image
        mix_image = {"image": _artist.artist.image, "color": _artist.artist.color}
        image = self.download_artist_image(_artist.artist)

        if image:
            mix_image["image"] = image

        mix = Mix(
            # the a prefix indicates that this is an artist mix
            id=f"a{userid}{artist['artisthash']}",
            title=artist["artist"] + " Radio",
            description=self.get_mix_description(mix_tracks, artist["artisthash"]),
            tracks=[t.trackhash for t in mix_tracks],
            sourcehash=sourcehash,
            userid=userid,
            extra={
                "type": "artist",
                "artisthash": artist["artisthash"],
                "sourcetracks": trackhashes,
                "image": mix_image,
                # NOTE: Save the similar albums and artists
                # Related to the source tracks that were used to create the mix
                # Will be useful when generating other homepage entries
                "albums": albums,
                "artists": artists,
            },
            timestamp=int(time.time()),
        )

        MixTable.insert_one(mix)
        return mix

    def download_artist_image(self, artist: Artist):
        try:
            res = requests.get(
                f"{self.server}/mix/image?artist={quote(artist.name)}&type=Artist"
            )
        except requests.exceptions.ConnectionError:
            return None

        if res.status_code == 200:
            filename = f"{artist.artisthash}_{int(time.time())}.webp"
            path = Paths().md_mixes_img_path / filename

            image = Image.open(BytesIO(res.content))
            aspect_ratio = image.width / image.height

            # resize to 512px
            md_width = 512
            md_height = int(md_width / aspect_ratio)

            image = image.resize((md_width, md_height), Image.LANCZOS)
            image.save(path, "webp")

            # resize to 256px
            sm_width = 256
            sm_height = int(sm_width / aspect_ratio)

            image = image.resize((sm_width, sm_height), Image.LANCZOS)
            small_path = Paths().sm_mixes_img_path / filename
            image.save(small_path, "webp")

            return filename

        return None

    def fallback_create_artist_mix(
        self,
        # artist: dict[str, str],
        similar_albums: list[str],
        similar_artists: list[str],
        omit_trackhashes: set[str],
        limit: int = 99,
    ):
        """
        Creates an artist mix by selecting random tracks from similar albums and artists.

        This is used when:
        - The local recommendation pass yields too few tracks.
        - When we need to dilute the mix to balance the artist distribution.

        :param similar_albums: A list of similar album weakhashes to select tracks from.
        :param similar_artists: A list of similar artist hashes to select tracks from.
        :param omit_trackhashes: A set of trackhashes to omit from the new tracklist.
        :param limit: The maximum number of tracks to select.
        """

        mixtracks = []
        albummatches = (
            a
            for a in AlbumStore.albummap.values()
            if a.album.albumhash in similar_albums or a.album.weakhash in similar_albums
        )

        for match in albummatches:
            if len(mixtracks) >= limit:
                return mixtracks

            albumtracks = [
                t
                for t in TrackStore.get_tracks_by_trackhashes(match.trackhashes)
                if t.weakhash not in omit_trackhashes
            ]

            if len(albumtracks) == 0:
                continue

            sample = random.sample(albumtracks, k=1)
            mixtracks.extend(sample)

        artistmatches = (
            a
            for a in ArtistStore.artistmap.values()
            if a.artist.artisthash in similar_artists
        )

        for match in artistmatches:
            if len(mixtracks) >= limit:
                return mixtracks

            artisttracks = [
                t
                for t in TrackStore.get_tracks_by_trackhashes(match.trackhashes)
                if t.weakhash not in omit_trackhashes
            ]

            if len(artisttracks) == 0:
                continue

            sample = random.sample(artisttracks, k=1)
            mixtracks.extend(sample)

        return mixtracks

    def get_mix_from_lastfm_data(self, artisthash: str, limit: int):
        """
        Creates a mix from the locally available lastfm similar artists data.

        The resulting mix is definitely expected to be of low quality.

        TODO: Maybe implement this!
        """
        pass

    @classmethod
    def get_track_mix(cls, mix: Mix):
        """
        Given a mix, returns the excess tracks as a custom mix.
        """

        # INFO: If the mix can't have more than 20 tracks, return None
        if len(mix.tracks) <= cls.MIX_TRACKS_LENGTH + 20:
            return None

        og_track = TrackStore.trackhashmap.get(mix.tracks[0])

        if not og_track:
            return None

        og_track = og_track.get_best()
        tracks = [og_track] + TrackStore.get_tracks_by_trackhashes(
            mix.tracks[cls.MIX_TRACKS_LENGTH :]
        )

        trackmix = Mix(
            id=f"t{mix.userid}{mix.extra['artisthash']}",
            title=og_track.title,
            description=cls.get_mix_description(tracks, mix.extra["artisthash"]),
            tracks=[t.trackhash for t in tracks],
            sourcehash=create_hash(*[t.trackhash for t in tracks]),
            userid=mix.userid,
            extra={
                "type": "track",
                "og_sourcehash": mix.sourcehash,
                "images": cls.get_custom_mix_images(tracks),
                "artists": None,
                "albums": None,
            },
        )
        trackmix.timestamp = mix.timestamp

        # INFO: Write track mix save state
        if mix.extra.get("trackmix_saved"):
            trackmix.saved = True

        return trackmix

    @classmethod
    def get_custom_mix_images(cls, tracks: list[Track]):
        first_album = tracks[0].albumhash
        first_img = {
            "image": first_album + ".webp",
            "type": "album",
            "color": AlbumStore.albummap[first_album].album.color,
        }

        seen = set()
        images = [first_img]

        for track in tracks[1:]:
            artisthash = track.artists[0]["artisthash"]

            if artisthash in seen:
                continue

            artist = ArtistStore.artistmap.get(artisthash)

            if not artist:
                continue

            seen.add(artisthash)

            image = {
                "image": artisthash + ".webp",
                "type": "artist",
                "color": artist.artist.color,
            }

            images.append(image)

            if len(images) == 3:
                break

        return images

    @staticmethod
    def get_because_items(mixes: list[Mix]):
        """
        Given a list of mixes, returns a list of artists that are similar to the
        artists in the mixes.
        """
        artists: dict[str, list[dict[str, str | int]]] = {}
        albums: dict[str, list[dict[str, str | int]]] = {}

        pivot_artist = None
        pivot_artist_index = None

        # Get pivot artist
        for index, mix in enumerate(mixes):
            artist = ArtistStore.artistmap.get(mix.extra["artisthash"])
            if not artist:
                continue

            pivot_artist = artist.artist
            pivot_artist_index = index
            break

        if not pivot_artist:
            return None, None

        for mix in mixes[pivot_artist_index:]:
            mix_artisthash = mix.extra["artisthash"]
            artists.setdefault(mix_artisthash, [])
            albums.setdefault(mix_artisthash, [])

            for artisthash in mix.extra["artists"]:
                artist = ArtistStore.artistmap.get(artisthash)

                if not artist:
                    continue

                artists[mix_artisthash].append(
                    {
                        "type": "artist",
                        "trackcount": artist.artist.trackcount,
                        "hash": artisthash,
                        "help_text": str(artist.artist.trackcount)
                        + ngettext(" track", " tracks", artist.artist.trackcount),
                    }
                )

            for albumhash in mix.extra["albums"]:
                album = AlbumStore.albummap.get(albumhash)

                if not album:
                    continue

                albums[mix_artisthash].append(
                    {
                        "type": "album",
                        "trackcount": album.album.trackcount,
                        "hash": albumhash,
                        "help_text": str(album.album.trackcount)
                        + ngettext(" track", " tracks", album.album.trackcount),
                    }
                )

            # INFO: Sort artists by trackcount
            artists[mix_artisthash] = sorted(
                artists[mix_artisthash],
                key=lambda x: x["trackcount"],
                reverse=True,
            )

            # INFO: Sort albums by trackcount
            albums[mix_artisthash] = sorted(
                albums[mix_artisthash],
                key=lambda x: x["trackcount"],
                reverse=True,
            )

        because_you_listened_to_artist = {
            "title": "Because you listened to "
            + pivot_artist.name,
            "items": albums[pivot_artist.artisthash][:15],
        }

        # Flatten list of artists and remove duplicates by artisthash
        all_artists = []
        seen = set()

        # for artist_list in artists.values():
        #     for artist in artist_list:
        #         if artist["hash"] not in seen:
        #             all_artists.append(artist)
        #             seen.add(artist["hash"])

        artists_you_might_like = {
            "title": "Artists you might like",
            "items": artists[pivot_artist.artisthash][:15],
        }

        return because_you_listened_to_artist, artists_you_might_like
