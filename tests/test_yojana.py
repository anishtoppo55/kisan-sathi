import pytest

from app.modules.yojana import FarmerProfile, check_all, check_scheme, load_schemes, match_schemes, summarize

SCHEMES = {s["id"]: s for s in load_schemes()}

T, F, N = True, False, None


def status(scheme_id, **profile):
    return check_scheme(SCHEMES[scheme_id], FarmerProfile(**profile), "hi-en")["status"]


@pytest.mark.parametrize("profile,expected", [
    (dict(owns_land=T, income_tax_payer=F, govt_employee_or_pensioner_10k=F), "eligible"),
    (dict(owns_land=T, income_tax_payer=T, govt_employee_or_pensioner_10k=F), "not_eligible"),   # IT payer excluded
    (dict(owns_land=T, income_tax_payer=F, govt_employee_or_pensioner_10k=T), "not_eligible"),   # pensioner/govt excluded
    (dict(owns_land=F, is_tenant=T, income_tax_payer=F, govt_employee_or_pensioner_10k=F), "not_eligible"),  # tenants excluded
    (dict(owns_land=T, income_tax_payer=F), "needs_check"),                                       # unknown field
    (dict(), "needs_check"),
])
def test_pm_kisan(profile, expected):
    assert status("pm-kisan", **profile) == expected


@pytest.mark.parametrize("profile,expected", [
    (dict(owns_land=T), "eligible"),
    (dict(owns_land=F, is_tenant=T), "eligible"),       # tenant / sharecropper can get KCC
    (dict(owns_land=F, is_tenant=F), "not_eligible"),
    (dict(owns_land=T, has_kcc=T), "already_enrolled"),
    (dict(owns_land=N, is_tenant=N), "needs_check"),
    (dict(owns_land=F, is_tenant=N), "needs_check"),
])
def test_kcc(profile, expected):
    assert status("kcc", **profile) == expected


@pytest.mark.parametrize("profile,expected", [
    (dict(owns_land=T, registered_on_state_portal=T), "eligible"),
    (dict(owns_land=T, registered_on_state_portal=F), "not_eligible"),
    (dict(is_tenant=T, registered_on_state_portal=N), "needs_check"),
])
def test_krishak_unnati(profile, expected):
    assert status("krishak-unnati", **profile) == expected


def test_pmfby_tenant_ok_and_soil_card_always():
    assert status("pmfby", owns_land=F, is_tenant=T) == "eligible"
    assert status("pmfby", owns_land=F, is_tenant=F) == "not_eligible"
    assert status("soil-health-card") == "eligible"


def test_reasons_and_questions_localised():
    r = check_scheme(SCHEMES["pm-kisan"], FarmerProfile(owns_land=True, income_tax_payer=True), "hi-IN")
    assert r["status"] == "not_eligible" and "आयकर" in r["reasons"][0]
    r = check_scheme(SCHEMES["pm-kisan"], FarmerProfile(owns_land=True), "hi-en")
    assert len(r["questions"]) == 2 and all("?" in q for q in r["questions"])


def test_check_all_sorted_and_summary():
    p = FarmerProfile(owns_land=True, income_tax_payer=True, govt_employee_or_pensioner_10k=False)
    results = check_all(list(SCHEMES.values()), p, "hi-en")
    order = [r["status"] for r in results]
    assert order == sorted(order, key=["eligible", "already_enrolled", "needs_check", "not_eligible"].index)
    s = summarize(results, "hi-en")
    assert "PM-Kisan" in s and "paatra nahi" in s


def test_profile_merge_keeps_known_values():
    a = FarmerProfile(owns_land=True, has_kcc=False)
    b = FarmerProfile(has_kcc=True, district="Durg")
    m = a.merged(b)
    assert m.owns_land is True and m.has_kcc is True and m.district == "Durg"


def test_match_schemes_keywords_then_top_hit():
    schemes = list(SCHEMES.values())
    assert [s["id"] for s in match_schemes("PM-Kisan ki kist", schemes, [])] == ["pm-kisan"]
    assert [s["id"] for s in match_schemes("koi sawal", schemes, ["pmfby"])] == ["pmfby"]
    assert match_schemes("koi sawal", schemes, []) == []


def test_schemes_json_is_consistent():
    for s in SCHEMES.values():
        for key in ("name", "benefit", "documents", "apply"):
            assert set(s[key]) >= {"hi-IN", "hi-en"}, (s["id"], key)
        assert s["link"].startswith("https://")
        for c in s["checks"]:
            fields = [x["field"] for x in c.get("any", [c])]
            assert all(f in FarmerProfile.model_fields for f in fields)
