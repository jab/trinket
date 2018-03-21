# -*- coding: utf-8 -*-

from curio import run, socket, tcp_server, timeout_after
from curio import TaskTimeout, TaskGroup
from autoroutes import Routes
from httptools import HttpParserUpgrade, HttpParserError, HttpRequestParser
from .request import Request
from .response import Response
from .http import HTTPStatus, HttpError
from .websockets import Websocket


class Upgrade:

    def __init__(self, request):
        self.request = request


class HTTPParser:

    __slots__ = ('parser', 'request', 'complete')
    
    def __init__(self):
        self.parser = HttpRequestParser(self)
        self.complete = False

    def data_received(self, data):
        self.parser.feed_data(data)

    def on_header(self, name, value):
        value = value.decode()
        if value:
            name = name.decode().upper()
            if name in self.request.headers:
                self.request.headers[name] += ', {}'.format(value)
            else:
                self.request.headers[name] = value

    def on_message_begin(self):
        self.complete = False
        self.request = Request()

    def on_url(self, url):
        self.request.url = url

    def on_headers_complete(self):
        self.request.keep_alive = self.parser.should_keep_alive()
        self.request.method = self.parser.get_method().decode().upper()
        self.complete = True


class ClientHandler:
    """This handler is spawned for each new connection.
    It can be kept alive as long as the timeout is respected.
    """
    
    __slots__ = ('app', 'httpparser', 'upgrade')
    
    max_field_size = 2**16
    
    def __init__(self, app):
        self.app = app
        self.httpparser = HTTPParser()
        self.upgrade = False

    async def receive(self, client):
        stream = client.makefile('rb')
        async for line in stream:
            if not line:
                break
            if len(line) > self.max_field_size:
                return HttpError(
                    HTTPStatus.BAD_REQUEST, 'Request headers too large.')
            # 2 sec tolerance while reading each line
            client._socket.settimeout(2)
            try:
                self.httpparser.parser.feed_data(line)
            except HttpParserError as exc:
                return HttpError(
                    HTTPStatus.BAD_REQUEST, 'Unparsable request.')
            except HttpParserUpgrade as upgrade:
                self.upgrade = True
            if not line.strip():
                # End of the headers section.
                break

        if self.httpparser.complete:
            return self.httpparser.request

    async def __call__(self, client, addr):
        async with client:
            try:
                keep_alive = True
                client._socket.settimeout(10.0)
                while keep_alive:
                    request = await self.receive(client)
                    if request is None:
                        break
                    if isinstance(request, HttpError):
                        await client.sendall(bytes(request))                   
                    else:
                        keep_alive = request.keep_alive
                        request.socket = client
                        client._socket.settimeout(None)
                        response = await self.app(request, self.upgrade)
                        await client.sendall(bytes(response))
                    if keep_alive:
                        # We answered. The socket timeout is reset.
                        client._socket.settimeout(10.0)
            except HttpError as exc:
                # An error occured during the processing of the request.
                # We write down an error for the client.
                await client.sendall(bytes(exc))
            except (ConnectionResetError, BrokenPipeError):
                # The client disconnected or the network is suddenly
                # unreachable.
                pass
            except socket.timeout:
                # Our socket timed out, due to the lack of activity.
                pass


class Granite(dict):

    def __init__(self):
        self.routes = Routes()

    async def on_error(self, request: Request, error):
        response = Response(self, request)
        if not isinstance(error, HttpError):
            error = HttpError(HTTPStatus.INTERNAL_SERVER_ERROR,
                              str(error).encode())
        response.status = error.status
        response.body = error.message
        return response

    async def lookup(self, request: Request):
        payload, params = self.routes.match(request.path)
        if not payload:
            raise HttpError(HTTPStatus.NOT_FOUND, request.path)
        # Uppercased in order to only consider HTTP verbs.
        handler = payload.get(request.method.upper(), None)
        if handler is None:
            raise HttpError(HTTPStatus.METHOD_NOT_ALLOWED)
        return handler, params, payload

    async def __call__(self, request: Request, upgrade=False) -> Response:
        try:
            found = await self.lookup(request)
            if found is not None:
                handler, params, payload = found
                if payload.get('websocket', False):
                    if not upgrade:
                        error = HttpError(
                            HTTPStatus.UPGRADE_REQUIRED,
                            'This is a websocket endpoint, please upgrade.')
                        response = await self.on_error(request, error)
                    else:
                        websocket = Websocket(request)
                        await websocket.upgrade()
                        async with TaskGroup(wait=any) as ws:
                            await ws.spawn(
                                handler, request, websocket, **params)
                            await ws.spawn(websocket.run)
                else:
                    response = Response(self, request)
                    await handler(request, response, **params)
        except Exception as error:
            response = await self.on_error(request, error)
        return response

    def route(self, path: str, methods: list=None, **extras: dict):
        if methods is None:
            methods = ['GET']

        def wrapper(func):
            payload = {method: func for method in methods}
            payload.update(extras)
            self.routes.add(path, **payload)
            return func

        return wrapper

    def websocket(self, path: str, **extras: dict):

        def wrapper(func):
            payload = {'GET': func, 'websocket': True}
            payload.update(extras)
            self.routes.add(path, **payload)
            return func

        return wrapper

    def serve(self, host='127.0.0.1', port=5000):
        handler = ClientHandler(self)
        try:
            run(tcp_server(host, port, handler))
        except KeyboardInterrupt:
            pass