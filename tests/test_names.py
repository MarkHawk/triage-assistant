from triage.names import lab_template_names, load_names


def test_defaults_load():
    company, systems = load_names()
    assert company == "Northgate Supplies"
    assert systems["staff-portal"].display == "Northgate Staff Portal"
    assert systems["expenses"].display == "Northgate Expenses"


def test_lab_names_are_preferred_when_present(tmp_path):
    t = tmp_path / "apps" / "saml-sp" / "templates"
    t.mkdir(parents=True)
    (t / "base.html").write_text("<title>Northgate Staff Hub</title>")
    assert lab_template_names(tmp_path) == {"staff-portal": "Northgate Staff Hub"}
    _, systems = load_names(tmp_path)
    assert systems["staff-portal"].display == "Northgate Staff Hub"
    assert systems["expenses"].display == "Northgate Expenses"  # nothing in the lab: default


def test_nothing_in_the_lab_means_defaults(tmp_path):
    assert lab_template_names(tmp_path) == {}
