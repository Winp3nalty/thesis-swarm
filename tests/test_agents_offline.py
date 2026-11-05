"""Offline tests of the v0.9b agents: Finance, Local Logistics, Static Web and the route-order check.
Run: python tests/test_agents_offline.py"""
import _paths  # noqa: F401  (puts the project folder on the import path)
from swarm.core import deps
deps.ensure()

import os

os.environ["SWARM_OFFLINE"] = "1"

from swarm.code_agents import finance
from swarm.code_agents import local_logistics
from swarm.code_agents import route_order
from swarm.code_agents import static_web
from swarm.code_agents.reality import load_cost_reference, _is_generic
from swarm.core.requirements_schema import load_scenario
from swarm.core.schemas import ExpeditionPlan


def _plan(days):
    return ExpeditionPlan.model_validate({"destination": "Shenzhen, China", "duration_days": len(days),
        "total_budget_eur": 1.0, "days": [{"day": i + 1, "focus_topic": "x", "activities": acts}
                                          for i, acts in enumerate(days)]})


def _act(loc, start="09:00", end="11:00", desc="Visit", cost=10.0):
    return {"start_time": start, "end_time": end, "location": loc, "activity_description": desc,
            "transit_mode": "Taxi", "cost_eur": cost}


SZ = load_cost_reference("shenzhen")


def test_finance_replaces_model_hotel_rows():
    plan = _plan([[_act("DJI", cost=50), _act("Hotel", "18:00", "20:00", "Dinner and hotel check-in", 25),
                   _act("Hotel", "20:00", "22:00", "Overnight stay", 40)],
                  [_act("HAX", cost=0), _act("Hotel", "20:00", "23:00", "Overnight stay", 40),
                   _act("Hotel", "21:00", "22:00", "Hotel check-in again", 40)],
                  [_act("Huaqiangbei", cost=5)]])
    notes = finance.agent_finance(plan, SZ, printer=lambda *_: None)
    assert "removed 4" in notes[0], notes
    for d in plan.days:
        locs = [a.location for a in d.activities]
        assert locs.count(finance.DAILY_LOCATION) == 1
        assert locs.count("Hotel") == (1 if d.day < 3 else 0), (d.day, locs)
    assert plan.days[0].activities[-1].activity_description == "Hotel check-in and overnight stay"
    # 55 visits + 2 nights x 40 + 3 days x 35 (25 meals + 10 transport)
    assert plan.total_budget_eur == 55 + 80 + 105, plan.total_budget_eur
    assert all(_is_generic(a.location) for d in plan.days for a in d.activities if a.location in ("Hotel", finance.DAILY_LOCATION))
    # idempotent: a second pass gives the same plan
    before = plan.model_dump()
    finance.agent_finance(plan, SZ, printer=lambda *_: None)
    assert plan.model_dump() == before


def test_finance_meals_row_does_not_overlap():
    plan = _plan([[_act("DJI", "09:00", "13:00"), _act("HAX", "13:00", "19:45")], [_act("OCT Loft", "06:00", "19:50")]])
    finance.agent_finance(plan, SZ, printer=lambda *_: None)
    from swarm.skills import run_skill
    for d in plan.days:
        res = run_skill("schedule_check", activities=[a.model_dump() for a in d.activities])
        assert res["result"]["ok"], (d.day, res["result"])


def test_finance_empty_day_stays_empty_and_no_table_is_a_noop():
    plan = _plan([[_act("DJI")], []])
    finance.agent_finance(plan, SZ, printer=lambda *_: None)
    assert plan.days[1].activities == []
    plan2 = _plan([[_act("DJI")]])
    assert finance.agent_finance(plan2, {}, printer=lambda *_: None) == ["no price table: nothing added"]
    assert len(plan2.days[0].activities) == 1


def test_finance_report_adds_transit_only_when_higher():
    req = load_scenario("shenzhen_3day")
    plan = _plan([[_act("DJI", cost=50)], [_act("HAX", cost=0)], [_act("Huaqiangbei", cost=5)]])
    finance.agent_finance(plan, SZ, printer=lambda *_: None)
    legs = lambda eur: {"legs": [{"cost_eur": eur}]}
    transit = [{"ok": True, "case": {"day": 1}, "result": legs(4.0)},      # below the 10 EUR allowance
               {"ok": True, "case": {"day": 2}, "result": legs(16.0)},     # 6 EUR above it
               {"ok": False, "case": {"day": 3}, "result": None}]
    rep = finance.finance_report(plan, req, SZ, transit)
    assert [x["transit_extra"] for x in rep["days"]] == [0.0, 6.0, 0.0], rep["days"]
    assert rep["total_eur"] == plan.total_budget_eur + 6.0 and rep["within_budget"]
    assert rep["rate"]["currency"] == "CNY" and rep["total_local"] > rep["total_eur"]
    assert "CNY" in finance.finance_md(rep) and "Transit extra" in finance.finance_md(rep)


def test_route_order_finds_the_shorter_order():
    stops = ["A", "B", "C", "D"]
    # A and C are close, B and D are close, A-B far
    m = [[0, 50, 5, 55], [50, 0, 55, 5], [5, 55, 0, 50], [55, 5, 50, 0]]
    best = route_order.best_order(m)
    assert best[0] == [0, 2, 3, 1] or best[0] == [0, 2, 1, 3] or best[1] < 110
    rep = route_order.route_report(stops, m)
    assert rep["current_minutes"] == 50 + 55 + 50 and rep["best_minutes"] < rep["current_minutes"]
    assert rep["saves_minutes"] > 10 and len(rep["far_legs"]) == 3
    assert "would save" in route_order.route_note(rep) and "not one cluster" in route_order.route_note(rep)
    unknown = route_order.route_report(stops, [[0, None, 5, 5], [None, 0, 5, 5], [5, 5, 0, 5], [5, 5, 5, 0]])
    assert not unknown["known"] and "unknown" in route_order.route_note(unknown)
    assert route_order.best_order([[0, 5], [5, 0]]) is None


def test_local_logistics_requires_sources():
    for city in ("shenzhen", "silicon_valley"):
        r = local_logistics.agent_local_logistics(city, printer=lambda *_: None)
        assert r["problems"] == [] and r["sources"] >= 4, (city, r["problems"])
    missing = local_logistics.agent_local_logistics("nowhere", printer=lambda *_: None)
    assert missing["info"] == {} and missing["problems"]


def test_static_web_builds_markdown_and_json():
    req = load_scenario("shenzhen_3day")
    plan = _plan([[_act("DJI", cost=50)], [_act("HAX")], [_act("Huaqiangbei")]])
    finance.agent_finance(plan, SZ, printer=lambda *_: None)
    fin = finance.finance_report(plan, req, SZ)
    local = local_logistics.agent_local_logistics("shenzhen", printer=lambda *_: None)
    coord = {"transit": [{"ok": False, "case": {"day": 1, "stops": ["DJI"]}, "result": None, "error": "boom"}],
             "outreach": []}
    site = static_web.agent_static_web(plan.model_dump(), req, "SUCCESS", fin, local, coord, ["a warning"], "v-test",
                                       printer=lambda *_: None)
    md, data = site["handbook.md"], site["handbook.json"]
    assert "# Expedition handbook" in md and "Cost report" in md and "Official sources to check" in md
    assert "a warning" in md and "Transit day 1 failed: boom" in md
    assert data["plan"]["days"] and data["finance"]["total_eur"] and data["problems"] and data["practical"]


def test_scenario_coordinates_are_used_for_places_osm_does_not_know():
    from swarm.code_agents import dispatcher
    from swarm.code_agents import reality
    from swarm.code_agents.plausibility import plausibility_check
    req = load_scenario("shenzhen_3day")
    assert req.known_coords("HAX") is None
    req2 = req.model_copy(update={"place_coordinates": {"HAX": (22.54, 113.95), "Ghost Lab": (22.5, 114.0)}})
    assert req2.known_coords("hax") == (22.54, 113.95)
    # plausibility: given coordinates count as found, with source 'scenario'
    res = plausibility_check(req2)
    hax = next(x for x in res["log"] if x["name"] == "HAX")
    assert hax["found"] is True and hax["source"] == "scenario"
    assert not any("'HAX'" in w for w in res["warnings"])
    # reality: a place without coordinates in OSM gets them from the scenario, so travel gaps can be checked
    plan = _plan([[_act("HAX", "09:00", "10:00"), _act("Ghost Lab", "10:05", "11:00")]])
    out = reality.reality_check(plan, ["HAX"], {}, "Shenzhen, China", None, req2.place_coordinates)
    assert any(x["name"] == "Ghost Lab" and x["source"] == "scenario" for x in out["log"]), out["log"]
    # dispatcher: drive times start from the given coordinates
    assert dispatcher._coords(["HAX", "Ghost Lab"], req2) == [(22.54, 113.95), (22.5, 114.0)]
    # a bad pair is refused
    try:
        req.model_validate({**req.model_dump(), "place_coordinates": {"X": [95.0, 10.0]}})
    except ValueError:
        pass
    else:
        raise AssertionError("latitude 95 must be rejected")


def test_handbook_header_has_no_duplicate_lines():
    req = load_scenario("shenzhen_3day")
    plan = _plan([[_act("DJI")], [_act("HAX")], [_act("Huaqiangbei")]])
    site = static_web.agent_static_web(plan.model_dump(), req, "SUCCESS", None, None, None, [], "v-test",
                                       printer=lambda *_: None)
    md = site["handbook.md"]
    assert md.count("**Days:**") == 1 and md.count("**Destination:**") == 1, md[:400]


def test_group_size_budget_and_prices_are_per_person():
    from swarm.code_agents.finance import finance_report, finance_md
    from swarm.code_agents.plausibility import plausibility_check
    from swarm.code_agents.reality import load_cost_reference
    req = load_scenario("shenzhen_3day")
    assert req.group_size == 1 and req.budget_per_person == req.budget_eur
    assert "per person" not in req.request_text(), "a single traveller keeps the old text"
    big = req.model_copy(update={"group_size": 4, "budget_eur": 4000.0})
    assert big.budget_per_person == 1000.0 and "Group of 4" in big.request_text() and "1000 EUR per person" in big.request_text()
    # a group budget that is too small per person is refused by the plausibility gate
    poor = req.model_copy(update={"group_size": 10, "budget_eur": 1000.0})
    hard = plausibility_check(poor)["hard"]
    assert hard and "per person" in hard[0], hard
    # the report multiplies the per-person total by the group
    plan = _plan([[_act("DJI", "09:00", "10:00")]])
    plan.days[0].activities[0].cost_eur = 100.0
    rep = finance_report(plan, big, load_cost_reference(big.cost_profile))
    assert rep["total_eur"] == 100.0 and rep["total_group_eur"] == 400.0 and rep["left_eur"] == 3600.0, rep
    assert "4 travellers = 400.00 EUR" in finance_md(rep)
    try:
        req.model_validate({**req.model_dump(), "group_size": 0})
    except ValueError:
        pass
    else:
        raise AssertionError("group_size 0 must be rejected")


def test_place_must_be_in_the_region_of_the_trip():
    from swarm.code_agents import geo, plausibility, reality
    sz = geo.load_geo("shenzhen")
    assert geo.km_outside((22.54, 114.05), sz) is None
    assert geo.km_outside((60.17, 24.94), sz) > 7000, "Helsinki is far from Shenzhen"
    assert geo.km_outside((60.17, 24.94), {}) is None and geo.load_geo("nowhere") == {}
    wrong = load_scenario("shenzhen_wrong_place")  # the shipped test scenario holds Helsinki
    assert "Helsinki" in wrong.must_visit and "Helsinki" not in load_scenario("shenzhen_3day").must_visit
    req = load_scenario("shenzhen_3day").model_copy(update={"must_visit": ["DJI", "Helsinki"]})
    calls = []
    def fake(name, **kw):
        calls.append((kw["place"], kw["city"]))
        if kw["place"] == "Helsinki" and kw["city"] == "":  # not found in the region, but exists far away
            return {"ok": True, "result": {"found": True, "lat": 60.17, "lon": 24.94, "source": "live"}}
        if kw["place"] == "Helsinki":
            return {"ok": True, "result": {"found": False, "source": "live"}}
        return {"ok": True, "result": {"found": True, "lat": 22.54, "lon": 113.95, "source": "live"}}
    real = plausibility.run_skill
    plausibility.run_skill = fake
    try:
        res = plausibility.plausibility_check(req)
    finally:
        plausibility.run_skill = real
    assert any("'Helsinki' is" in h and "km from the centre of Shenzhen" in h and "place_coordinates" in h
               for h in res["hard"]), res["hard"]
    assert not any("DJI" in h for h in res["hard"])
    # the Planner's own pick is checked in the plan too, and blamed on the Planner
    plan = _plan([[_act("DJI", "09:00", "10:00"), _act("Helsinki", "10:30", "11:30")]])
    real_skill = reality.run_skill
    reality.run_skill = lambda name, **kw: {"ok": True, "result": (
        {"found": True, "lat": 60.17, "lon": 24.94, "source": "live"} if kw.get("place") == "Helsinki"
        else {"found": True, "lat": 22.54, "lon": 113.95, "source": "live", "minutes": 5.0})}
    try:
        out = reality.reality_check(plan, ["DJI"], {}, "Shenzhen, China", None, None, sz, "Shenzhen")
    finally:
        reality.run_skill = real_skill
    assert any("Place 'Helsinki' is" in i for i in out["logistics_issues"]), out["logistics_issues"]
    from types import SimpleNamespace as NS
    from swarm.llm_agents.justice import blame_planner_for_chosen_places
    rough = NS(days=[NS(target_hubs=[NS(hub_name="Helsinki")])])
    blame_planner_for_chosen_places(out, rough, NS(region="Shenzhen, China"))
    assert not out["logistics_issues"] and any("Place 'Helsinki' is" in i for i in out["planner_issues"]), out


def test_one_hotel_for_the_whole_trip():
    from swarm.code_agents import hotels
    from swarm.code_agents.finance import agent_finance, is_lodging
    from swarm.code_agents.reality import _is_generic
    sz = hotels.load_hotels("shenzhen")
    assert len(sz) >= 2 and hotels.load_hotels("nowhere") == [] and hotels.choose_hotel("nowhere") is None
    first = hotels.choose_hotel("shenzhen")
    assert first == sz[0] and hotels.choose_hotel("shenzhen") == first, "same hotel every time"
    assert hotels.choose_hotel("shenzhen", sz[1]["name"].upper()) == sz[1], "the scenario can name another listed hotel"
    assert hotels.choose_hotel("shenzhen", "Not A Listed Hotel") == first
    assert load_scenario("shenzhen_7day").hotel == ""
    plan = _plan([[_act("DJI", "09:00", "10:00")], [_act("HAX", "09:00", "10:00")], [_act("InnoX", "09:00", "10:00")]])
    agent_finance(plan, {"hotel_night_eur": 40, "meals_per_day_eur": 25, "local_transport_per_day_eur": 10},
                  printer=lambda *_: None, chosen_hotel=first)
    rows = [a for d in plan.days for a in d.activities if a.location.startswith("Hotel")]
    assert len(rows) == 2 and {a.location for a in rows} == {f"Hotel: {first['name']}"}, rows
    assert all(is_lodging(a) and _is_generic(a.location) for a in rows), "the named hotel still counts as lodging"


def test_meal_times_are_mandatory():
    from swarm.code_agents import meal_times
    from swarm.code_agents.finance import agent_finance
    meals = meal_times.load_meals("shenzhen")
    assert list(meals) == ["breakfast", "lunch", "dinner"] and meal_times.load_meals("nowhere") == {}
    assert "Mandatory meal times" in meal_times.meal_prompt_text(meals) and meal_times.meal_prompt_text({}) == ""
    # a visit through lunch (11:00-15:00) keeps its longer side; one through dinner is cut at the window
    plan = _plan([[_act("DJI", "09:00", "12:30"), _act("HAX", "13:30", "16:00")],
                  [_act("InnoX", "16:00", "18:45")]])
    notes = meal_times.fix_meal_times(plan, meals)
    assert [(a.start_time, a.end_time) for a in plan.days[0].activities] == [("09:00", "12:00"), ("14:00", "16:00")], notes
    assert plan.days[1].activities[0].end_time == "18:30"
    assert meal_times.meal_problems(plan, meals) == []
    # too short after the cut: left alone, then reported with the soft marker
    short = _plan([[_act("DJI", "11:45", "12:30")]])
    assert meal_times.fix_meal_times(short, meals) == []
    probs = meal_times.meal_problems(short, meals)
    assert len(probs) == 1 and "overlaps a meal time" in probs[0] and "lunch 12:00-14:00" in probs[0], probs
    from swarm.code_agents.plan_quality import SOFT_MARKERS
    assert any(m in probs[0] for m in SOFT_MARKERS)
    # the Finance meals row sits in the lunch window
    p2 = _plan([[_act("DJI", "09:00", "11:30")]])
    agent_finance(p2, {"hotel_night_eur": 40, "meals_per_day_eur": 25, "local_transport_per_day_eur": 10},
                  printer=lambda *_: None, meals_windows=meals)
    row = next(a for a in p2.days[0].activities if a.location == "Meals and local transport")
    assert (row.start_time, row.end_time) == ("12:00", "14:00"), (row.start_time, row.end_time)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nAll {len(tests)} agent tests passed.")
