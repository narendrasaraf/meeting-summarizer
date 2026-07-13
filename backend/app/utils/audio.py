def is_audio_file(content: bytes) -> bool:
    """Sniff magic bytes of popular audio/video container formats."""
    if len(content) < 12:
        return False
        
    # 1. WAV: "RIFF" .... "WAVE"
    if content.startswith(b"RIFF") and content[8:12] == b"WAVE":
        return True
        
    # 2. MP3: starts with "ID3" (ID3v2) or frame sync \xff\xfb, \xff\xf3, \xff\xf2
    if (content.startswith(b"ID3") or 
            content.startswith(b"\xff\xfb") or 
            content.startswith(b"\xff\xf3") or 
            content.startswith(b"\xff\xf2")):
        return True
        
    # 3. FLAC: "fLaC"
    if content.startswith(b"fLaC"):
        return True
        
    # 4. Ogg/OPUS: "OggS"
    if content.startswith(b"OggS"):
        return True
        
    # 5. WebM/MKV: EBML header \x1a\x45\xdf\xa3
    if content.startswith(b"\x1a\x45\xdf\xa3"):
        return True
        
    # 6. M4A/MP4: "ftyp" at offset 4
    if content[4:8] == b"ftyp":
        return True
        
    return False
