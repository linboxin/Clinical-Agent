from app.contracts.enums import DateBasis, OverallStatus, Phase, StudyType
from app.contracts.plan import TimeScope
from app.ctgov.compile import compile_cohort, quote
from tests.factories import filters

ALL_TIME = TimeScope(date_basis=DateBasis.START_DATE, year_from=None, year_to=None)


def test_drug_uses_field_scoped_search_on_name_and_other_names() -> None:
    params = compile_cohort(filters(drug_name="trastuzumab deruxtecan"), ALL_TIME)
    assert params == {
        "query.term": '(AREA[InterventionName]"trastuzumab deruxtecan" '
        'OR AREA[InterventionOtherName]"trastuzumab deruxtecan")'
    }


def test_all_filters_combine_with_and() -> None:
    f = filters(
        condition="lung cancer",
        sponsor="Merck Sharp & Dohme LLC",
        country="France",
        trial_phase=[Phase.PHASE2, Phase.PHASE3],
        study_type=StudyType.INTERVENTIONAL,
        overall_status=[OverallStatus.RECRUITING, OverallStatus.NOT_YET_RECRUITING],
    )
    time = TimeScope(date_basis=DateBasis.START_DATE, year_from=2015, year_to=None)
    params = compile_cohort(f, time)
    assert params["query.cond"] == '"lung cancer"'
    assert params["filter.overallStatus"] == "RECRUITING,NOT_YET_RECRUITING"
    assert params["query.term"] == (
        'AREA[LeadSponsorName]"Merck Sharp & Dohme LLC" AND AREA[LocationCountry]"France" '
        "AND AREA[Phase](PHASE2 OR PHASE3) AND AREA[StudyType]INTERVENTIONAL "
        "AND AREA[StartDate]RANGE[2015-01-01,MAX]"
    )


def test_year_range_uses_the_date_basis_field() -> None:
    time = TimeScope(date_basis=DateBasis.FIRST_POSTED, year_from=None, year_to=2019)
    assert compile_cohort(filters(), time) == {
        "query.term": "AREA[StudyFirstPostDate]RANGE[MIN,2019-12-31]"
    }


def test_no_filters_means_no_query() -> None:
    assert compile_cohort(filters(), ALL_TIME) == {}


def test_quote_strips_query_syntax_from_user_text() -> None:
    # A user (or the model) cannot break out of the phrase to inject Essie operators.
    assert quote('x" OR AREA[Phase]PHASE1 (y)') == '"x OR AREA Phase PHASE1 y"'
