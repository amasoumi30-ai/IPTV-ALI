# IPTV-ALI channel checks

Non-destructive validator for the 68-channel Persian test playlist on the owner's netcup VPS. Does not modify Jellyfin, passwords, the master playlist or parental settings.

## One command (SSH terminal on the netcup VPS)

    curl -fsSL https://raw.githubusercontent.com/amasoumi30-ai/IPTV-ALI/main/tools/iptv_ali_maintainer.py -o /root/iptv_ali_maintainer.py && python3 /root/iptv_ali_maintainer.py

Requires Python 3 and ffprobe (provided by FFmpeg). Tests streams FROM THE VPS network. Tries the existing URL first and community/public alternatives only when needed, including published Telewebion URLs for IRIB.

Output:
- /opt/iptv/playlists/IPTV-ALI-STATUS.txt — channel-by-channel probe report (no stream URLs, secrets or tokens).
- /opt/iptv/playlists/IPTV-ALI-VERIFIED.m3u — separate playlist of channels with detectable audio or video at test time.

The original IPTV-ALI-PERSIAN-TEST.m3u is not overwritten. Jellyfin will NOT switch automatically: change the tuner path only after reviewing results. Successful probing is not a 24/7 uptime guarantee or proof of redistribution rights.

Official GEM Group website lists channels: https://www.gemgroup.tv/. Public stable direct streams may be unavailable. Do not bypass source restrictions, expose private tokens, or restream without permission. Manoto announced stopping live broadcasts on 24 May 2026, so stale URLs should not be assumed active.
