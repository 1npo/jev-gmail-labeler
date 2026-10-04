import base64

from jev_gmail_labeler.google_api import message_parser as mp


def b64(text: str, charset='utf-8') -> str:
    return base64.urlsafe_b64encode(text.encode(charset)).decode()


def test_plain_only(make_raw_message):
    m = mp.parse_message(make_raw_message(plain='Hello\n\n\n\nworld  there'))
    assert m.body_text == 'Hello\n\nworld there'
    assert m.id == 'm1'
    assert m.thread_id == 't-m1'
    assert m.history_id == '100'
    assert m.internal_date_ms == 1_700_000_000_000
    assert m.label_ids == ('INBOX',)
    assert m.sender == 'A <a@x.com>'
    assert m.to == 'me@example.com'
    assert m.cc == ''
    assert m.reply_to == ''
    assert m.subject == 'Hi'
    assert m.date.startswith('Tue')


def test_html_only(make_raw_message):
    m = mp.parse_message(
        make_raw_message(html='<html><body><p>Hi <b>you</b></p></body></html>')
    )
    assert m.body_text == 'Hi\nyou'


def test_html_wins_over_plain(make_raw_message):
    m = mp.parse_message(make_raw_message(plain='plain text', html='<p>html text</p>'))
    assert m.body_text == 'html text'


def test_html_that_is_empty_falls_back_to_plain(make_raw_message):
    m = mp.parse_message(make_raw_message(plain='plain text', html='<img src="x">'))
    assert m.body_text == 'plain text'


def test_script_style_head_title_removed():
    html = (
        '<html><head><title>T</title><style>p{}</style></head>'
        '<body><script>alert(1)</script><p>Body</p></body></html>'
    )
    assert mp.html_to_text(html) == 'Body'


def test_nested_multipart():
    payload = {
        'mimeType': 'multipart/mixed',
        'parts': [
            {
                'mimeType': 'multipart/related',
                'parts': [
                    {
                        'mimeType': 'multipart/alternative',
                        'parts': [
                            {'mimeType': 'text/plain', 'body': {'data': b64('deep')}}
                        ],
                    }
                ],
            }
        ],
    }
    assert mp.extract_body_text(payload) == 'deep'


def test_single_part_payload():
    payload = {'mimeType': 'text/plain', 'body': {'data': b64('solo')}}
    assert mp.extract_body_text(payload) == 'solo'


def test_attachment_skipped_by_filename_and_attachment_id():
    payload = {
        'mimeType': 'multipart/mixed',
        'parts': [
            {'mimeType': 'text/plain', 'body': {'data': b64('body')}},
            {'mimeType': 'text/plain', 'filename': 'a.txt', 'body': {'data': b64('NO1')}},
            {'mimeType': 'text/plain', 'body': {'attachmentId': 'x', 'data': b64('NO2')}},
        ],
    }
    assert mp.extract_body_text(payload) == 'body'


def test_attachment_in_fixture(make_raw_message):
    m = mp.parse_message(make_raw_message(plain='hi', attachment=True))
    assert m.body_text == 'hi'


def test_invisible_characters_removed():
    text = 'a​b‌c‍d‎e‏f⁠g﻿h­i͏j k'
    assert mp.html_to_text(f'<p>{text}</p>') == 'abcdefghij k'


def test_double_escaped_entity():
    assert mp.html_to_text('<p>Tom &amp;amp; Jerry</p>') == 'Tom & Jerry'


def test_plain_text_is_not_unescaped(make_raw_message):
    m = mp.parse_message(make_raw_message(plain='a &amp; b'))
    assert m.body_text == 'a &amp; b'


def test_whitespace_collapse():
    html = '<p>a \t  b</p><p>   </p><p>  c  </p><p>d</p>'
    assert mp.html_to_text(html) == 'a b\n\nc\nd'


def test_many_newlines_collapse_to_two():
    assert mp._clean_text('x \n \n\n\n\n y') == 'x\n\ny'


def test_snippet_fallback(make_raw_message):
    m = mp.parse_message(make_raw_message(snippet='Tom &amp; Jerry &#39;s'))
    assert m.body_text == "Tom & Jerry 's"


def test_empty_message(make_raw_message):
    assert mp.parse_message(make_raw_message()).body_text == ''


def test_encoded_word_headers(make_raw_message):
    m = mp.parse_message(
        make_raw_message(
            sender='=?UTF-8?B?SsO8cmdlbg==?= <j@x.com>', subject='=?utf-8?q?Caf=C3=A9?='
        )
    )
    assert m.sender == 'Jürgen <j@x.com>'
    assert m.subject == 'Café'


def test_bad_header_falls_back(monkeypatch):
    def boom(value):
        raise ValueError

    monkeypatch.setattr(mp, 'decode_header', boom)
    assert mp.decode_header_value('=?bad?=') == '=?bad?='


def test_headers_case_insensitive(make_raw_message):
    raw = make_raw_message()
    raw['payload']['headers'] = [
        {'name': 'SUBJECT', 'value': 'Loud'},
        {'name': 'reply-to', 'value': 'r@x.com'},
        {'name': 'Cc', 'value': 'c@x.com'},
    ]
    m = mp.parse_message(raw)
    assert (m.subject, m.reply_to, m.cc) == ('Loud', 'r@x.com', 'c@x.com')
    assert m.sender == ''
    assert m.to == ''
    assert m.date == ''


def test_charset_from_content_type():
    payload = {
        'mimeType': 'text/plain',
        'headers': [
            {'name': 'Content-Type', 'value': 'text/plain; charset="ISO-8859-1"'}
        ],
        'body': {'data': b64('café', 'latin-1')},
    }
    assert mp.extract_body_text(payload) == 'café'


def test_unknown_charset_falls_back_to_utf8():
    payload = {
        'mimeType': 'text/plain',
        'headers': [{'name': 'Content-Type', 'value': 'text/plain; charset=nonsense-9'}],
        'body': {'data': b64('café')},
    }
    assert mp.extract_body_text(payload) == 'café'


def test_content_type_without_charset_and_other_headers():
    payload = {
        'mimeType': 'text/plain',
        'headers': [
            {'name': 'X-Other', 'value': 'charset=latin-1'},
            {'name': 'Content-Type', 'value': 'text/plain'},
        ],
        'body': {'data': b64('café')},
    }
    assert mp.extract_body_text(payload) == 'café'


def test_invalid_bytes_replaced():
    data = base64.urlsafe_b64encode(b'ok \xff\xfe').decode()
    payload = {'mimeType': 'text/plain', 'body': {'data': data}}
    assert mp.extract_body_text(payload).startswith('ok �')


def test_missing_padding_handled():
    data = b64('abcd').rstrip('=')
    payload = {'mimeType': 'text/plain', 'body': {'data': data}}
    assert mp.extract_body_text(payload) == 'abcd'


def test_non_text_mime_ignored():
    payload = {'mimeType': 'image/png', 'body': {'data': b64('x')}}
    assert mp.extract_body_text(payload) == ''


def test_missing_optional_fields():
    m = mp.parse_message({'id': 'z'})
    assert m.id == 'z'
    assert m.internal_date_ms == 0
    assert m.label_ids == ()
    assert m.body_text == ''
    assert m.thread_id == ''
