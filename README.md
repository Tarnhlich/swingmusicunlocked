# 🎵 Swing Music (Unlocked Edition)

<div align="center">
  <img src="https://raw.githubusercontent.com/swingmx/swingmusic/master/.github/images/logo-fill.light.svg" height="64"/>
</div>

<div align="center">
  <b>Self-hosted music streaming server</b>
</div>

<div align="center">
  <img src="https://img.shields.io/github/v/release/swingmx/swingmusic" />
</div>

---

Swing Music is a fast and beautiful self-hosted music streaming server.  
This fork provides a **community edition focused on local-first playback and self-hosted usage**, without cloud or license-based features.
Forked from : https://github.com/swingmx/swingmusic

---

## ✨ Features

- Daily Mixes (local algorithm)
- Metadata normalization
- Album versioning (Deluxe, Remaster, etc)
- Related artists & albums
- Folder-based browsing
- Web UI
- Silence detection & crossfade
- Collections
- Listening statistics
- Last.fm scrobbling
- Multi-user support
- Cross-platform (Linux, Windows, macOS*)

---

## 🚫 Removed / Not Included

This build intentionally excludes:

- Cloud services
- License activation system
- Device registration
- Premium-only plugins
- External account dependencies

---

## 🐳 Docker

### Docker Compose

```yaml
services:
  swingmusic:
    image: your-registry/swingmusic:latest
    container_name: swingmusic
    ports:
      - "1970:1970"
    volumes:
      - /path/to/music:/music
      - /path/to/config:/config
    environment:
      - SWINGMUSIC_PORT=1970
      - SWINGMUSIC_DEVICE_NAME=Home Server
    restart: unless-stopped


## 🤝 Contributing

Contributions are welcome.

Please open issues or pull requests for:

UI improvements
performance optimizations
bug fixes
new playback features
## 📄 License

Swing Music is licensed under AGPLv3.

See the LICENSE file for details.

## ⚠️ Note

This fork is a community-maintained self-hosted distribution and is not affiliated with the original maintainers.