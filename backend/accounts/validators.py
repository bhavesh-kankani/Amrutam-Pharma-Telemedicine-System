import re
from urllib.parse import unquote
from django.core.exceptions import ValidationError

# Indian mobile: 10 digits starting with 6, 7, 8, or 9
INDIAN_MOBILE_REGEX = re.compile(r"^[6-9]\d{9}$")
E164_INDIAN_REGEX = re.compile(r"^\+91[6-9]\d{9}$")


def normalize_and_validate_phone(phone: str) -> str:
    """Normalizes any valid Indian phone number into standard +91XXXXXXXXXX format.

    Handles:
      - 10 digits: "9988776655" -> "+919988776655"
      - Grouping spaces: "99887 76655", "9988 776 655" -> "+919988776655"
      - Dots & dashes: "9988.776.655", "99887-76655" -> "+919988776655"
      - Domestic trunk '0': "09988776655", "(0) 99887 76655" -> "+919988776655"
      - International format: "+91 99887 76655", "(+91) 9988776655" -> "+919988776655"
      - Exit code format: "0091 99887 76655", "0091-9988776655" -> "+919988776655"
      - Prefix without '+': "919988776655" -> "+919988776655"
      - URL-encoded strings: "%2B919988776655" -> "+919988776655"

    Rejects:
      - Numbers starting with 0-5 for the mobile subscriber part
      - Incomplete or oversized numbers
      - Landlines or toll-free numbers (e.g., 1800, 040-XXXXXXX)
    """
    if not phone or not str(phone).strip():
        raise ValidationError("Phone number cannot be empty.")

    # 1. URL decode in case raw query parameters were forwarded
    raw_str = unquote(str(phone).strip())

    # 2. Strip all visual delimiters: spaces, tabs, dashes, dots, parentheses, brackets, slashes
    cleaned = re.sub(r"[\s\-\.\(\)\[\]/,]", "", raw_str)

    # 3. Extract the 10-digit mobile number by stripping recognized prefixes
    if cleaned.startswith("+91"):
        digits_only = cleaned[3:]
    elif cleaned.startswith("0091"):
        digits_only = cleaned[4:]
    elif cleaned.startswith("91") and len(cleaned) == 12:
        digits_only = cleaned[2:]
    elif cleaned.startswith("0") and len(cleaned) == 11:
        digits_only = cleaned[1:]
    else:
        # Assumed raw 10 digits (or invalid format to be caught next)
        digits_only = cleaned

    # 4. Enforce strict Indian mobile rules: exactly 10 digits starting with 6, 7, 8, or 9
    if not INDIAN_MOBILE_REGEX.match(digits_only):
        raise ValidationError(
            f"Invalid Indian mobile number '{phone}'. "
            "Must be a valid 10-digit mobile number starting with 6, 7, 8, or 9."
        )

    return f"+91{digits_only}"
