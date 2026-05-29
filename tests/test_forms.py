"""Safe form parsing (web/forms.py).

Malformed numeric input on a direct POST/GET must degrade to a sane default
instead of 500-ing the request.
"""


def test_form_int_basic():
    from web.forms import form_int
    assert form_int("3") == 3
    assert form_int("  7 ") == 7          # surrounding whitespace tolerated
    assert form_int("abc", 5) == 5        # non-numeric -> default
    assert form_int(None) == 0            # missing -> default 0
    assert form_int("", 9) == 9           # empty -> default
    assert form_int("3.5", 1) == 1        # float string is not an int -> default


def test_form_int_clamp():
    from web.forms import form_int
    assert form_int("99", 1, lo=1, hi=5) == 5
    assert form_int("-4", lo=0) == 0
    assert form_int("4", 1, lo=1, hi=5) == 4
    assert form_int("abc", 2, lo=1, hi=5) == 2   # default stays in range


def test_audit_limit_non_numeric_does_not_500(staff):
    """The audit log's ?limit= used to do a bare int() — a non-numeric value
    must now fall back to the default instead of raising a 500."""
    r = staff.get("/staff/audit?limit=not-a-number")
    assert r.status_code == 200


def test_admin_settings_non_numeric_cap_does_not_500(staff):
    """A garbage xp_cap_amount on the settings POST must not 500 — form_int
    falls back to the default (350)."""
    r = staff.post(
        "/staff/admin/settings",
        data={"_csrf": "dev-csrf-token", "active_ruleset": "standard",
              "revenant_families": "", "require_sheet_on_create": "on",
              "xp_cap_amount": "twelve"},
        follow_redirects=False,
    )
    # 303 redirect back to the settings page — never a 500.
    assert r.status_code == 303
