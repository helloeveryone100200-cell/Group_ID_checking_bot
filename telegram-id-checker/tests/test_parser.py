from parser import extract_gmail, extract_id, parse_id


def test_supported_headers_and_separators() -> None:
    cases = {
        "ID: 12345": "12345",
        "Id: 12345": "12345",
        "iD: 12345": "12345",
        "id: 12345": "12345",
        "ID - 12345": "12345",
        "ID-12345": "12345",
        "ID : 12345": "12345",
        "ID=12345": "12345",
    }
    for message, expected in cases.items():
        assert extract_id(message) == expected


def test_values_preserve_leading_zeroes_letters_and_hyphens() -> None:
    assert extract_id("ID: 000123") == "000123"
    assert extract_id("ID: ABC123") == "ABC123"
    assert extract_id("ID: A-12345") == "A-12345"
    assert extract_id("ID: 123-ABC") == "123-ABC"
    assert extract_id("ID: 123") != extract_id("ID: 000123")


def test_empty_and_missing_values_are_ignored() -> None:
    assert extract_id("") is None
    assert extract_id("   \n\t") is None
    assert extract_id("ID:") is None
    assert extract_id("ID -") is None
    assert extract_id("ID=") is None
    assert extract_id("Name: Aung\nPhone: 091234567") is None
    assert extract_id("No ID here") is None


def test_only_standalone_id_header_matches() -> None:
    for message in (
        "Client ID: 12345",
        "Telegram ID: 12345",
        "User ID: 12345",
        "Order ID: 12345",
        "Identity: 12345",
        "ID Card: 12345",
    ):
        assert extract_id(message) is None


def test_multiline_and_whitespace() -> None:
    assert extract_id("Name: Aung\n  ID : \t 000123  \nPhone: 091234567") == "000123"


def test_multiple_headers_are_ambiguous() -> None:
    parsed = parse_id("ID: 12345\nName: Aung\nID: 67890")
    assert parsed.value is None
    assert parsed.ambiguous is True
    assert parsed.header_count == 2


def test_empty_header_still_makes_multiple_headers_ambiguous() -> None:
    parsed = parse_id("ID:\nID: 12345")
    assert parsed.value is None
    assert parsed.ambiguous is True


def test_extract_gmail_from_spaced_field_and_preserve_value() -> None:
    message = (
        "Gmail                  -  Mimifentahun\\n"
        "Tele name         -   @Muluyeme\\n"
        "Username         -    @muluyeme\\n"
        "ID                - 251904933464"
    )
    assert extract_gmail(message) == "Mimifentahun"


def test_extract_gmail_supports_case_and_separators() -> None:
    assert extract_gmail("Name: A\\ngMaIl: sample@example.com\\nID: 123") == "sample@example.com"
    assert extract_gmail("GMAIL = value-123") == "value-123"


def test_missing_empty_or_negative_gmail_is_omitted() -> None:
    assert extract_gmail("ID: 123\\nName: A") is None
    assert extract_gmail("Gmail -") is None
    assert extract_gmail("Gmail - no") is None
    assert extract_gmail("Gmail - N/A") is None


def test_multiple_gmail_fields_are_treated_as_ambiguous() -> None:
    assert extract_gmail("Gmail - first\\nGmail - second") is None
