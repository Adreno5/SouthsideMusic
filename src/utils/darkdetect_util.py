from darkdetect import isDark as isDarkDarkdetect

_is_dark = isDarkDarkdetect()

def isDark():
    return _is_dark

def isLight():
    return not _is_dark