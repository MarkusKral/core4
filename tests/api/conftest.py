from contextlib import closing
from inspect import iscoroutinefunction
import tornado.ioloop
import tornado.testing
import tornado.simple_httpclient
import json
import pytest


# def get_test_timeout(pyfuncitem):
#     timeout = pyfuncitem.config.option.async_test_timeout
#     marker = pyfuncitem.get_marker('timeout')
#     if marker:
#         timeout = marker.kwargs.get('seconds', timeout)
#     return timeout


def pytest_addoption(parser):
    parser.addoption('--async-test-timeout', type=float,
                     help=('timeout in seconds before failing the test '
                           '(default is no timeout)'))
    parser.addoption('--app-fixture', default='app',
                     help=('fixture name returning a tornado application '
                           '(default is "app")'))


@pytest.mark.tryfirst
def pytest_pycollect_makeitem(collector, name, obj):
    if collector.funcnamefilter(name) and iscoroutinefunction(obj):
        return list(collector._genfunctions(name, obj))


@pytest.mark.tryfirst
def pytest_pyfunc_call(pyfuncitem):
    funcargs = pyfuncitem.funcargs
    testargs = {arg: funcargs[arg] for arg in pyfuncitem._fixtureinfo.argnames}

    if not iscoroutinefunction(pyfuncitem.obj):
        pyfuncitem.obj(**testargs)
        return True

    try:
        event_loop = funcargs['io_loop']
    except KeyError:
        event_loop = next(io_loop())

    if not isinstance(event_loop, tornado.ioloop.IOLoop):
        raise TypeError("unsupported event loop:  %s" % type(event_loop))

    event_loop.run_sync(
        lambda: pyfuncitem.obj(**testargs),
        #timeout=get_test_timeout(pyfuncitem),
    )
    return True


@pytest.fixture
def io_loop():
    """
    Create a new `tornado.ioloop.IOLoop` for each test case.
    """
    loop = tornado.ioloop.IOLoop()
    loop.make_current()
    yield loop
    loop.clear_current()
    loop.close(all_fds=True)


@pytest.fixture
def http_server_port():
    """
    Port used by `http_server`.
    """
    return tornado.testing.bind_unused_port()


@pytest.yield_fixture
def http_server(request, io_loop, http_server_port):
    """Start a tornado HTTP server that listens on all available interfaces.

    You must create an `app` fixture, which returns
    the `tornado.web.Application` to be tested.

    Raises:
        FixtureLookupError: tornado application fixture not found
    """
    fix = request.config.option.app_fixture
    http_app = request.getfixturevalue(fix)
    server = tornado.httpserver.HTTPServer(http_app)
    server.add_socket(http_server_port[0])

    yield server

    server.stop()

    if hasattr(server, 'close_all_connections'):
        io_loop.run_sync(server.close_all_connections,
                         #timeout=request.config.option.async_test_timeout
        )


class AsyncHTTPServerClient(tornado.simple_httpclient.SimpleAsyncHTTPClient):

    def initialize(self, *, http_server=None):
        super().initialize()
        self._http_server = http_server

    def fetch(self, path, headers=None, **kwargs):
        """
        Fetch `path` from test server, passing `kwargs` to the `fetch`
        of the underlying `tornado.simple_httpclient.SimpleAsyncHTTPClient`.
        """
        if headers is None:
            headers = {}
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        return super().fetch(self.get_url(path), headers=headers, **kwargs)

    def post(self, path, body="", headers=None, **kwargs):
        if isinstance(body, dict):
            data = json.dumps(body)
        else:
            data = body
        kwargs["method"] = "POST"
        return self.fetch(path, body=data, headers=headers, **kwargs)

    def put(self, path, body="", headers=None, **kwargs):
        if isinstance(body, dict):
            data = json.dumps(body)
        else:
            data = body
        kwargs["method"] = "PUT"
        return self.fetch(path, body=data, headers=headers, **kwargs)

    def get_protocol(self):
        return 'http'

    def get_http_port(self):
        for sock in self._http_server._sockets.values():
            return sock.getsockname()[1]

    def get_url(self, path):
        return '%s://127.0.0.1:%s%s' % (self.get_protocol(),
                                        self.get_http_port(), path)


@pytest.fixture
def http_server_client(http_server):
    """
    Create an asynchronous HTTP client that can fetch from `http_server`.
    """
    with closing(AsyncHTTPServerClient(http_server=http_server)) as client:
        yield client


@pytest.fixture
def http_client(http_server):
    """
    Create an asynchronous HTTP client that can fetch from anywhere.
    """
    with closing(tornado.httpclient.AsyncHTTPClient()) as client:
        yield client