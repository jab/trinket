import pytest
from trinket.request import Channel
from trinket.http import HTTPError
from trinket.testing import RequestForger
from io import BytesIO


@pytest.fixture
def parser():
    return Channel(None)


pytestmark = pytest.mark.curio


async def test_parse_multipart(parser):
    parser.data_received(
        b'POST /post HTTP/1.1\r\n'
        b'Host: localhost:1707\r\n'
        b'User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:54.0) '
        b'Gecko/20100101 Firefox/54.0\r\n'
        b'Origin: http://localhost:7777\r\n'
        b'Content-Length: 180\r\n'
        b'Content-Type: multipart/form-data; boundary=foofoo\r\n'
        b'\r\n'
        b'--foofoo\r\n'
        b'Content-Disposition: form-data; name=baz; filename="baz.png"\r\n'
        b'Content-Type: image/png\r\n'
        b'\r\n'
        b'abcdef\r\n'
        b'--foofoo\r\n'
        b'Content-Disposition: form-data; name="text1"\r\n'
        b'\r\n'
        b'abc\r\n--foofoo--')
    await parser.request.parse_body()
    assert parser.request.form.get('text1') == 'abc'
    assert parser.request.files.get('baz').filename == 'baz.png'
    assert parser.request.files.get('baz').content_type == b'image/png'
    assert parser.request.files.get('baz').read() == b'abcdef'


async def test_parse_multipart_filename_star(parser):
    parser.data_received(
        b'POST /post HTTP/1.1\r\n'
        b'Host: localhost:1707\r\n'
        b'User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:54.0) '
        b'Gecko/20100101 Firefox/54.0\r\n'
        b'Origin: http://localhost:7777\r\n'
        b'Content-Length: 195\r\n'
        b'Content-Type: multipart/form-data; boundary=foofoo\r\n'
        b'\r\n'
        b'--foofoo\r\n'
        b'Content-Disposition: form-data; name=baz; '
        b'filename*="iso-8859-1\'\'baz-\xe9.png"\r\n'
        b'Content-Type: image/png\r\n'
        b'\r\n'
        b'abcdef\r\n'
        b'--foofoo\r\n'
        b'Content-Disposition: form-data; name="text1"\r\n'
        b'\r\n'
        b'abc\r\n--foofoo--')
    await parser.request.parse_body()
    assert parser.request.form.get('text1') == 'abc'
    assert parser.request.files.get('baz').filename == 'baz-é.png'
    assert parser.request.files.get('baz').content_type == b'image/png'
    assert parser.request.files.get('baz').read() == b'abcdef'


async def test_parse_unparsable_multipart(parser):
    parser.data_received(
        b'POST /post HTTP/1.1\r\n'
        b'Host: localhost:1707\r\n'
        b'User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:54.0) '
        b'Gecko/20100101 Firefox/54.0\r\n'
        b'Origin: http://localhost:7777\r\n'
        b'Content-Length: 18\r\n'
        b'Content-Type: multipart/form-data; boundary=foofoo\r\n'
        b'\r\n'
        b'--foofoo--foofoo--')
    with pytest.raises(HTTPError) as e:
        await parser.request.parse_body()
    assert e.value.message == b'Unparsable multipart body'


async def test_parse_unparsable_urlencoded(parser):
    parser.data_received(
        b'POST /post HTTP/1.1\r\n'
        b'Host: localhost:1707\r\n'
        b'User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:54.0) '
        b'Gecko/20100101 Firefox/54.0\r\n'
        b'Origin: http://localhost:7777\r\n'
        b'Content-Length: 3\r\n'
        b'Content-Type: application/x-www-form-urlencoded\r\n'
        b'\r\n'
        b'foo')
    with pytest.raises(HTTPError) as e:
        await parser.request.parse_body()
    assert e.value.message == b'Unparsable urlencoded body'


@pytest.mark.parametrize('params', [
    ('filecontent', 'afile.txt'),
    (b'filecontent', 'afile.txt'),
    (BytesIO(b'filecontent'), 'afile.txt'),
])
async def test_post_multipart(parser, params):
    request = RequestForger.post(
        '/test', files={'afile': params})
    parser.data_received(request)
    await parser.request.parse_body()
    assert parser.request.files.get('afile').filename == 'afile.txt'


async def test_post_urlencoded(parser):
    request = RequestForger.post(
        '/test', body={'foo': 'bar'},
        content_type='application/x-www-form-urlencoded')
    parser.data_received(request)
    await parser.request.parse_body()
    assert parser.request.form.get(b'foo') == b'bar'


async def test_post_urlencoded_list(parser):
    request = RequestForger.post(
        '/test', body=[('foo', 'bar'), ('foo', 'baz')],
        content_type='application/x-www-form-urlencoded')
    parser.data_received(request)
    await parser.request.parse_body()
    assert parser.request.form.list(b'foo') == [b'bar', b'baz']
