
def wave2rgb(wavelength):
    """ Convert a nanometer integer wavelength into a (r,g,b) value, with
    value ranges [0..255]
    """

    w = int(wavelength)

    # colour
    if w >= 380 and w < 440:
        r = -(w - 440.0) / (440.0 - 350.0)
        g = 0.0
        b = 1.0
    elif w >= 440 and w < 490:
        r = 0.0
        g = (w - 440.0) / (490.0 - 440.0)
        b = 1.0
    elif w >= 490 and w < 510:
        r = 0.0
        g = 1.0
        b = -(w - 510.0) / (510.0 - 490.0)
    elif w >= 510 and w < 580:
        r = (w - 510.0) / (580.0 - 510.0)
        g = 1.0
        b = 0.0
    elif w >= 580 and w < 645:
        r = 1.0
        g = -(w - 645.0) / (645.0 - 580.0)
        b = 0.0
    elif w >= 645 and w <= 780:
        r = 1.0
        g = 0.0
        b = 0.0
    else:
        r = 0.0
        g = 0.0
        b = 0.0

    # intensity correction
    if w >= 380 and w < 420:
        sss = 0.3 + 0.7 * (w - 350) / (420 - 350)
    elif w >= 420 and w <= 700:
        sss = 1.0
    elif w > 700 and w <= 780:
        sss = 0.3 + 0.7 * (780 - w) / (780 - 700)
    else:
        sss = 0.0
    sss *= 255

    return int(sss*r), int(sss*g), int(sss*b)