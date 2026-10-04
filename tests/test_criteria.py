import copy
from pathlib import Path

import pytest

from jev_gmail_labeler import criteria as crit
from jev_gmail_labeler.criteria import (
    EXAMPLE_CRITERIA,
    NO_MATCH_DESCRIPTION,
    NO_MATCH_ID,
    load_criteria,
    parse_criteria,
)
from jev_gmail_labeler.errors import CriteriaError


def errors_of(data) -> list[str]:
    with pytest.raises(CriteriaError) as e:
        parse_criteria(data)
    return e.value.errors


def with_(criteria_data, **changes):
    data = copy.deepcopy(criteria_data)
    data.update(changes)
    return data


def test_example_parses():
    c = parse_criteria(EXAMPLE_CRITERIA)
    assert len(c.categories) == 28
    assert c.category('other') is None
    assert c.category('credit_info').description.startswith(
        'An email from a financial ins'
    )
    assert 'friend request' in c.category('networking_invite').description
    assert c.category('order_shipped').label_name == 'order_shipped'


def test_parse_minimal_defaults():
    c = parse_criteria({'version': 1, 'categories': [{'id': 'a'}]})
    assert c.instructions == crit.DEFAULT_INSTRUCTIONS
    assert c.label_prefix == ''
    assert c.min_confidence == 0.0
    assert c.uncertain_label is None
    assert c.no_match_label is None
    assert c.categories[0].description is None
    assert c.categories[0].label_name == 'a'


def test_parse_fixture(criteria):
    assert criteria.min_confidence == 0.5
    assert criteria.uncertain_label == 'Jev/Unsure'
    assert [c.label_name for c in criteria.categories] == [
        'Jev/receipt',
        'Jev/Reading',
        None,
    ]


def test_derived_label_is_category_id():
    c = parse_criteria({'version': 1, 'categories': [{'id': 'order_shipped'}]})
    assert c.categories[0].label_name == 'order_shipped'


def test_derived_label_uses_explicit_prefix():
    c = parse_criteria(
        {
            'version': 1,
            'label_prefix': 'Jev',
            'categories': [{'id': 'receipt'}],
        }
    )
    assert c.categories[0].label_name == 'Jev/receipt'


def test_prefix_empty_means_no_prefix():
    c = parse_criteria(
        {
            'version': 1,
            'label_prefix': '',
            'no_match_label': 'Misc',
            'categories': [{'id': 'a', 'label': 'Mail/A'}],
        }
    )
    assert c.categories[0].label_name == 'Mail/A'
    assert c.no_match_label == 'Misc'


def test_description_forms():
    data = {
        'version': 1,
        'categories': [
            {'id': 'a', 'description': {'what': 'x', 'not_for': 'y'}},
            {'id': 'b', 'description': ['x', 'y']},
            {'id': 'c', 'description': None},
        ],
    }
    c = parse_criteria(data)
    assert c.categories[0].description == {'what': 'x', 'not_for': 'y'}
    assert c.categories[1].description == ['x', 'y']


def test_choice_criteria_appends_none_last(criteria):
    options = criteria.choice_criteria()
    assert list(options) == ['receipt', 'news', 'personal', NO_MATCH_ID]
    assert options[NO_MATCH_ID] == NO_MATCH_DESCRIPTION
    assert options['news'] == 'A news article'


def test_category_lookup(criteria):
    assert criteria.category('news').label_name == 'Jev/Reading'
    assert criteria.category('zzz') is None


def test_managed_label_names(criteria_data):
    data = with_(criteria_data, no_match_label='Other')
    assert parse_criteria(data).managed_label_names() == {
        'Jev/receipt',
        'Jev/Reading',
        'Jev/Unsure',
        'Jev/Other',
    }


def test_shared_labels_allowed():
    data = {
        'version': 1,
        'categories': [{'id': 'a', 'label': 'S'}, {'id': 'b', 'label': 'S'}],
    }
    assert parse_criteria(data).managed_label_names() == {'S'}


def test_root_not_object():
    assert errors_of([]) == ['(root): must be a JSON object']


def test_unknown_keys_at_every_level(criteria_data):
    data = with_(criteria_data, extra_key=1)
    data['categories'][0]['bogus'] = 1
    errs = errors_of(data)
    assert 'extra_key: unknown key' in errs
    assert 'categories[0].bogus: unknown key' in errs


def test_version_required_and_wrong(criteria_data):
    del criteria_data['version']
    assert 'version: required' in errors_of(criteria_data)
    assert 'version: must be 1 (got 2)' in errors_of(with_(criteria_data, version=2))
    assert any(
        e.startswith('version') for e in errors_of(with_(criteria_data, version=True))
    )


@pytest.mark.parametrize('value', ['', '   ', 5])
def test_instructions_invalid(criteria_data, value):
    assert 'instructions: must be a non-empty string' in errors_of(
        with_(criteria_data, instructions=value)
    )


def test_custom_instructions(criteria_data):
    c = parse_criteria(with_(criteria_data, instructions='Pick one'))
    assert c.instructions == 'Pick one'


@pytest.mark.parametrize('value', ['/x', 'x/', ' x', 'x ', 'a//b'])
def test_prefix_invalid(criteria_data, value):
    assert any(
        e.startswith('label_prefix:')
        for e in errors_of(with_(criteria_data, label_prefix=value))
    )


def test_prefix_not_string(criteria_data):
    assert 'label_prefix: must be a string' in errors_of(
        with_(criteria_data, label_prefix=3)
    )


@pytest.mark.parametrize('value', [-0.1, 1.1, 'x', True])
def test_min_confidence_invalid(criteria_data, value):
    assert any(
        e.startswith('min_confidence')
        for e in errors_of(with_(criteria_data, min_confidence=value))
    )


@pytest.mark.parametrize('key', ['uncertain_label', 'no_match_label'])
@pytest.mark.parametrize(
    ('value', 'fragment'),
    [
        ('', 'must not be empty'),
        ('a' * 201, 'at most 200'),
        ('a//b', '"//"'),
        ('/a', 'start or end'),
        ('a/', 'start or end'),
        (' a', 'start or end'),
        (5, 'must be a string or null'),
    ],
)
def test_label_values_invalid(criteria_data, key, value, fragment):
    errs = errors_of(with_(criteria_data, **{key: value}))
    assert any(e.startswith(f'{key}:') and fragment in e for e in errs)


@pytest.mark.parametrize(
    'name', ['INBOX', 'sent', 'Spam', 'CATEGORY_PROMOTIONS', 'category_x']
)
def test_system_labels_rejected_without_prefix(criteria_data, name):
    errs = errors_of(with_(criteria_data, label_prefix='', uncertain_label=name))
    assert any('reserved by Gmail' in e for e in errs)


def test_system_label_derived_from_id_without_prefix():
    errs = errors_of({'version': 1, 'label_prefix': '', 'categories': [{'id': 'inbox'}]})
    assert errs == ['categories[0].label: "inbox" is reserved by Gmail']


def test_categories_required_type_and_size(criteria_data):
    del criteria_data['categories']
    assert 'categories: required' in errors_of(criteria_data)
    assert 'categories: must be an array' in errors_of(
        with_(criteria_data, categories={})
    )
    assert 'categories: must have 1 to 254 items' in errors_of(
        with_(criteria_data, categories=[])
    )
    many = [{'id': f'c{i}'} for i in range(255)]
    assert 'categories: must have 1 to 254 items' in errors_of(
        with_(criteria_data, categories=many)
    )


def test_254_categories_ok():
    many = [{'id': f'c{i}'} for i in range(254)]
    assert len(parse_criteria({'version': 1, 'categories': many}).categories) == 254


def test_category_not_object(criteria_data):
    assert 'categories[0]: must be an object' in errors_of(
        with_(criteria_data, categories=['x'])
    )


def test_category_id_rules():
    errs = errors_of(
        {
            'version': 1,
            'categories': [
                {'id': 'Order Shipped'},
                {'id': 'news'},
                {'id': 'news'},
                {'description': 'x'},
                {'id': 5},
            ],
        }
    )
    assert (
        'categories[0].id: must match ^[a-z][a-z0-9_]{0,63}$ (got "Order Shipped")'
        in errs
    )
    assert 'categories[2].id: duplicate id "news"' in errs
    assert 'categories[3].id: required' in errs
    assert any(e.startswith('categories[4].id') for e in errs)


def test_category_description_invalid():
    errs = errors_of(
        {
            'version': 1,
            'categories': [{'id': 'a', 'description': ''}, {'id': 'b', 'description': 5}],
        }
    )
    assert 'categories[0].description: must not be empty' in errs
    assert any(e.startswith('categories[1].description') for e in errs)


def test_category_label_invalid():
    errs = errors_of(
        {'version': 1, 'categories': [{'id': 'a', 'label': ''}, {'id': 'b', 'label': 4}]}
    )
    assert 'categories[0].label: must not be empty' in errs
    assert 'categories[1].label: must be a string or null' in errs


def test_multiple_errors_reported_together():
    errs = errors_of({'version': 3, 'min_confidence': 9, 'categories': [], 'zzz': 1})
    assert len(errs) == 4


def test_criteria_error_str_includes_source():
    with pytest.raises(CriteriaError) as e:
        parse_criteria({}, source='my.json')
    assert str(e.value).startswith('my.json:\n  ')


def test_load_criteria_ok(monkeypatch, criteria_data):
    monkeypatch.setattr(crit.files, 'read_json', lambda p: criteria_data)
    assert len(load_criteria(Path('/c.json')).categories) == 3


def test_load_criteria_missing(monkeypatch):
    def boom(p):
        raise FileNotFoundError(p)

    monkeypatch.setattr(crit.files, 'read_json', boom)
    with pytest.raises(CriteriaError) as e:
        load_criteria(Path('/c.json'))
    assert e.value.errors == ['file not found']
    assert str(e.value).startswith('/c.json:')


def test_load_criteria_bad_json(monkeypatch):
    def boom(p):
        raise ValueError('/c.json: invalid JSON: x')

    monkeypatch.setattr(crit.files, 'read_json', boom)
    with pytest.raises(CriteriaError) as e:
        load_criteria(Path('/c.json'))
    assert 'invalid JSON' in e.value.errors[0]
