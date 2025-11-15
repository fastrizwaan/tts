import mpv
print([n for n in dir(mpv) if 'render' in n or 'gl' in n])

