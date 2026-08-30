"""Gentagelse ved forbigående fejl i ai._kald().

Den ugentlige kørsel er søndag kl. 8. Går den i fejl, står madplanen tom til
nogen opdager det — vi ramte en 503 hvor nøglen var helt i orden. Testen kører
mod en lokal server der spiller Anthropic på en dårlig dag.
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import ai, config

SVAR_OK = {"content": [{"type": "tool_use", "input": {"retter": [{"navn": "A"}]}}]}
FEJL = {"type": "error", "error": {"type": "api_error", "message": "overloaded"}}
FEJL_NOEGLE = {"type": "error",
               "error": {"type": "authentication_error", "message": "API key is invalid."}}


@pytest.fixture
def falsk_api(monkeypatch):
    """Server der svarer efter et manuskript. Returnerer antal modtagne kald."""
    plan, kaldt = [], []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            kode, krop, hoveder = plan[min(len(kaldt), len(plan) - 1)]
            kaldt.append(kode)
            self.send_response(kode)
            for k, v in (hoveder or {}).items():
                self.send_header(k, v)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(krop).encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "ANTHROPIC_URL",
                        "http://127.0.0.1:%d/v1/messages" % srv.server_address[1])
    monkeypatch.setattr(ai, "PAUSER", (0.01, 0.02))  # ingen grund til at vente i en test

    def opsaet(*svar):
        plan[:] = list(svar)
        kaldt.clear()
        return kaldt

    yield opsaet
    srv.shutdown()


def _kald():
    return asyncio.run(ai._kald("s", "b", ai.VAERKTOEJ_FORSLAG, 100))


def test_succes_med_det_samme(falsk_api):
    kaldt = falsk_api((200, SVAR_OK, None))
    assert _kald() == {"retter": [{"navn": "A"}]}
    assert len(kaldt) == 1


def test_503_een_gang_saa_ok(falsk_api):
    kaldt = falsk_api((503, FEJL, None), (200, SVAR_OK, None))
    assert _kald()
    assert len(kaldt) == 2


def test_503_hele_vejen_opgiver(falsk_api):
    kaldt = falsk_api((503, FEJL, None))
    with pytest.raises(RuntimeError, match="problemer lige nu"):
        _kald()
    assert len(kaldt) == ai.FORSOEG


def test_429_respekterer_retry_after(falsk_api):
    kaldt = falsk_api((429, FEJL, {"retry-after": "0"}), (200, SVAR_OK, None))
    assert _kald()
    assert len(kaldt) == 2


@pytest.mark.parametrize("kode", [400, 401, 403, 404])
def test_4xx_gentages_ikke(falsk_api, kode):
    """En forkert nøgle bliver ikke rigtig af at spørge igen."""
    kaldt = falsk_api((kode, FEJL_NOEGLE, None))
    with pytest.raises(RuntimeError):
        _kald()
    assert len(kaldt) == 1


def test_401_giver_handlingsanvisende_besked(falsk_api):
    falsk_api((401, FEJL_NOEGLE, None))
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        _kald()


def test_svar_uden_vaerktoejskald(falsk_api):
    falsk_api((200, {"content": [{"type": "text", "text": "hej"}]}, None))
    with pytest.raises(RuntimeError, match="værktøjskald"):
        _kald()
