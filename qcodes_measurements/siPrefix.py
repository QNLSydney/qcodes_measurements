import math
import re

# Print a floating-point number in engineering notation.
# Ported from [C version][1] written by
# Jukka “Yucca” Korpela <jkorpela@cs.tut.fi>.
#
# From: https://github.com/cfobel/si-prefix/tree/master
#
# [1]: http://www.cs.tut.fi/~jkorpela/c/eng.html
# Added si_parse function
# Sebastian Pauka

SI_PREFIX_UNITS = "yzafpnµm kMGTPEZY"
CRE_SI_NUMBER = re.compile(
    r"^\s*(?P<float_like>[+-]?(?:\d+(?:[.]\d*)?(?:[eE][+-]?\d+)?|[.]\d+(?:[eE][+-]?\d+)?))\s*"
    r"(?P<si_unit>[yzafpnµmkMGTPEZY])?\s*"
    r"(?P<unit>.*)$"
)
MU_LIKE = re.compile("[𝛍µ𝝁𝜇𝞵μ𝝻u]")


def si_prefix_scale(si_unit: str) -> int:
    """
    Parameters
    ----------
    si_unit : str
        SI unit character, i.e., one of "yzafpnµm kMGTPEZY".

    Returns
    -------
    int
        Multiple associated with `si_unit`, e.g., 1000 for `si_unit=k`.
    """
    return 10 ** si_prefix_expof10(si_unit)


def si_prefix_expof10(si_unit: str) -> int:
    """
    Parameters
    ----------
    si_unit : str
        SI unit character, i.e., one of "yzafpnµm kMGTPEZY".

    Returns
    -------
    int
        Exponent of the power of ten associated with `si_unit`, e.g., 3 for
        `si_unit=k` and -6 for `si_unit=µ`.
    """
    prefix_levels = (len(SI_PREFIX_UNITS) - 1) // 2
    return 3 * (SI_PREFIX_UNITS.index(si_unit) - prefix_levels)


def prefix(expof10: int) -> str:
    """
    Args:

        expof10 : Exponent of a power of 10 associated with a SI unit
            character.

    Returns:

        str : One of the characters in "yzafpnum kMGTPEZY".
    """
    prefix_levels = (len(SI_PREFIX_UNITS) - 1) // 2
    si_level = expof10 // 3

    if abs(si_level) > prefix_levels:
        raise ValueError("Exponent out range of available prefixes.")
    return SI_PREFIX_UNITS[si_level + prefix_levels]


def split(value: int | float) -> tuple[float, int]:
    """
    Split `value` into value and "exponent-of-10", where "exponent-of-10" is a
    multiple of 3.  This corresponds to SI prefixes.

    Returns tuple, where the second value is the "exponent-of-10" and the first
    value is `value` divided by the "exponent-of-10".

    Args
    ----
    value : int, float
        Input value.
    precision : int
        Number of digits after decimal place to include.

    Returns
    -------
    tuple
        The second value is the "exponent-of-10" and the first value is `value`
        divided by the "exponent-of-10".

    Examples
    --------

    .. code-block:: python

        si_prefix.split(0.04781)   ->  (47.8, -3)
        si_prefix.split(4781.123)  ->  (4.8, 3)

    See :func:`si_format` for more examples.
    """
    if value in (0, 0.0):
        return 0.0, 0

    expof10 = math.log10(abs(value))
    # Truncate to the lower factor of 3
    if expof10 >= 0:
        expof10 = (expof10 // 3) * 3
    else:
        if expof10 % 3 == 0:
            expof10 += 3
        expof10 = -((-expof10 + 3) // 3) * 3

    value *= 10 ** (-expof10)
    return value, int(expof10)


def si_format(value: float, precision: int = 1, separator: str = " ") -> str:
    """
    Format value to string with SI prefix, using the specified precision.

    Parameters
    ----------
    value : int, float
        Input value.
    precision : int
        Number of digits after decimal place to include.
    format_str : str or unicode
        Format string where ``{prefix}`` and ``{value}`` represent the SI
        prefix and the value (scaled according to the prefix), respectively.
        The default format matches the `SI prefix style`_ format.
    exp_str : str or unicode
        Format string where ``{expof10}`` and ``{value}`` represent the
        exponent of 10 and the value (scaled according to the exponent of 10),
        respectively.  This format is used if the absolute exponent of 10 value
        is greater than 24.

    Returns
    -------
    unicode
        :data:`value` formatted according to the `SI prefix style`_.

    Examples
    --------

    For example, with `precision=2`:

    .. code-block:: python

        1e-27 --> 1.00e-27
        1.764e-24 --> 1.76 y
        7.4088e-23 --> 74.09 y
        3.1117e-21 --> 3.11 z
        1.30691e-19 --> 130.69 z
        5.48903e-18 --> 5.49 a
        2.30539e-16 --> 230.54 a
        9.68265e-15 --> 9.68 f
        4.06671e-13 --> 406.67 f
        1.70802e-11 --> 17.08 p
        7.17368e-10 --> 717.37 p
        3.01295e-08 --> 30.13 n
        1.26544e-06 --> 1.27 u
        5.31484e-05 --> 53.15 u
        0.00223223 --> 2.23 m
        0.0937537 --> 93.75 m
        3.93766 --> 3.94
        165.382 --> 165.38
        6946.03 --> 6.95 k
        291733 --> 291.73 k
        1.22528e+07 --> 12.25 M
        5.14617e+08 --> 514.62 M
        2.16139e+10 --> 21.61 G
        9.07785e+11 --> 907.78 G
        3.8127e+13 --> 38.13 T
        1.60133e+15 --> 1.60 P
        6.7256e+16 --> 67.26 P
        2.82475e+18 --> 2.82 E
        1.1864e+20 --> 118.64 E
        4.98286e+21 --> 4.98 Z
        2.0928e+23 --> 209.28 Z
        8.78977e+24 --> 8.79 Y
        3.6917e+26 --> 369.17 Y
        1.55051e+28 --> 15.51e+27
        6.51216e+29 --> 651.22e+27
    """
    svalue, expof10 = split(value)

    try:
        return f"{svalue:.{precision}f}{separator}{prefix(expof10)}"
    except ValueError:
        return f"{svalue:.{precision}f}e{expof10:+g}"


def si_parse(value: str) -> tuple[float, str]:
    """
    Parse a value expressed using SI prefix units to a floating point number and optional unit

    Parameters
    ----------
    value : str or unicode
        Value expressed using SI prefix units (as returned by :func:`si_format`
        function).
    """

    # Try parsing as a float first
    try:
        return float(value), ""
    except ValueError:
        pass

    # Convert mu and all variants to the correct mu, and check if we look like an SI number
    value = MU_LIKE.sub("µ", value)
    match = CRE_SI_NUMBER.match(value)

    if not match:
        raise ValueError(f"Could not parse as SI formatted number: {value}")

    # Convert to float
    si_unit = match["si_unit"] if match["si_unit"] else " "
    scale = si_prefix_scale(si_unit)
    return float(match["float_like"]) * scale, match["unit"]
