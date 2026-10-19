#!/usr/bin/env python3
"""GTD HTTP client. Credentials come from the selected private cloud environment.

Usage: python tools/gtd_agent.py GET /reviews/daily
       python tools/gtd_agent.py POST /inbox/capture < request.json
No credential is accepted as a command-line argument or written to output.
"""
import json
import os
import re
import secrets
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

API = '/api/agent/v1'
PATH = re.compile(r'^/(?:capabilities|workspaces|reviews/(?:daily|weekly)|items(?:/[A-Za-z0-9_-]+(?:/(?:complete|trash|restore|comments))?)?|inbox(?:/capture|/[A-Za-z0-9_-]+/(?:draft|resolve))?|captures/[A-Za-z0-9_-]+|openapi\.json)$')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Never forward credentials to a redirect, including a same-origin one.
        raise urllib.error.HTTPError(request.full_url, code, 'Redirect rejected', headers, fp)


class Client:
    def __init__(self):
        self.origin = os.environ.get('GTD_AGENT_ORIGIN','https://gtd.sergeantipov.ru').rstrip('/')
        value = urllib.parse.urlsplit(self.origin)
        if value.scheme != 'https' or not value.netloc or value.username or value.password or value.path or value.query or value.fragment:
            raise ValueError('GTD_AGENT_ORIGIN должен быть HTTPS origin без пути и учётных данных')
        self.token = os.environ.get('GTD_AGENT_TOKEN','')
        if not self.token or any(c in self.token for c in '\r\n'):
            raise ValueError('В этой среде нет GTD_AGENT_TOKEN. Выберите личную облачную среду GTD с настроенным доступом')
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def request(self, method, path, data=None):
        method = method.upper()
        if method not in {'GET','POST','PATCH','PUT'}:
            raise ValueError('Допустимы GET, POST, PATCH, PUT')
        url = urllib.parse.urlsplit(path)
        if url.scheme or url.netloc or url.fragment or not PATH.fullmatch(url.path) or '\\' in path or any(c in path for c in '\r\n'):
            raise ValueError('Допустим только известный путь GTD Agent API')
        if method != 'GET' and (not isinstance(data,dict) or not data.get('request_key')):
            raise ValueError('Для записи нужен объект JSON с request_key')
        payload = json.dumps(data,ensure_ascii=False,allow_nan=False).encode() if data is not None else None
        headers = {'Authorization':'Bearer '+self.token,'Accept':'application/json','User-Agent':'personal-gtd-agent/1.0'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.origin+API+path,data=payload,headers=headers,method=method)
        with self.opener.open(request,timeout=30) as response:
            raw = response.read(16_000_001)
            if len(raw) > 16_000_000:
                raise ValueError('Ответ слишком большой; запросите меньшую страницу')
            return json.loads(raw)


def main():
    key = None
    try:
        if len(sys.argv) != 3:
            raise ValueError('Использование: gtd_agent.py METHOD /path; JSON для записи передаётся через stdin')
        method, path = sys.argv[1:]
        data = None
        if method.upper() != 'GET':
            raw = sys.stdin.buffer.read(262145)
            if len(raw) > 262144:
                raise ValueError('Запрос превышает 256 КиБ')
            data = json.loads(raw)
            if not isinstance(data,dict):
                raise ValueError('Ожидается объект JSON')
            key = data.setdefault('request_key',secrets.token_hex(16))
            # Capture the key BEFORE a potentially ambiguous network failure.
            print(json.dumps({'request_key':key,'state':'sending'},ensure_ascii=False),file=sys.stderr,flush=True)
        result = Client().request(method,path,data)
        print(json.dumps({'ok':True,'request_key':key,'result':result},ensure_ascii=False,indent=2))
    except urllib.error.HTTPError as exc:
        codes = {401:'Доступ отозван или ключ неверен',403:'Недостаточно прав',409:'Конфликт: прочитайте актуальные данные',429:'Лимит запросов; учтите Retry-After'}
        print(json.dumps({'ok':False,'status':exc.code,'request_key':key,
                          'message':codes.get(exc.code,'Запрос отклонён сервером'),
                          'request_id':exc.headers.get('X-Request-ID'), 'retry_after':exc.headers.get('Retry-After'),
                          'write_confirmed':False},ensure_ascii=False))
        sys.exit(2)
    except (urllib.error.URLError,TimeoutError,OSError):
        print(json.dumps({'ok':False,'request_key':key,'write_confirmed':False,
                          'message':'Нет подтверждённого ответа. Для записи повторите ТОТ ЖЕ запрос с ТОЙ ЖЕ request_key'},ensure_ascii=False))
        sys.exit(2)
    except (ValueError,TypeError):
        print(json.dumps({'ok':False,'request_key':key,'message':'Неверные параметры или в выбранной среде не настроен доступ GTD'},ensure_ascii=False))
        sys.exit(2)


if __name__ == '__main__':
    main()
