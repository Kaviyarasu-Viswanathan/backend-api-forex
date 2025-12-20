from typing import Dict, Generator, List, Any
from uuid import uuid4
from time import sleep, time
from threading import Thread
from json import loads, dumps
from random import getrandbits
from websocket import WebSocketApp
import os
import sys
from curl_cffi import requests as curl_requests

from urllib.parse import urlparse

class Perplexity:
    """
    A client for interacting with the Perplexity AI API via WebSockets.
    """
    def __init__(self, token: str = None, proxy_url: str = None) -> None:
        self.session = curl_requests.Session(impersonate="chrome120")
        self.request_headers: Dict[str, str] = {
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        self.token = token
        self.proxy_url = proxy_url or os.environ.get("PROXY_URL")
        
        # Configure Proxies for HTTP requests
        if self.proxy_url:
            self.session.proxies = {
                "http": self.proxy_url,
                "https": self.proxy_url
            }
            print(f"[*] Using Proxy: {self.proxy_url}", file=sys.stderr)

        self.session.headers.update(self.request_headers)
        
        if self.token:
            self.session.cookies.set("__Secure-next-auth.session-token", self.token, domain=".perplexity.ai")

        self.timestamp: str = format(getrandbits(32), "08x")
        
        print("[*] Initializing session via polling...", file=sys.stderr)
        url_polling = f"https://www.perplexity.ai/socket.io/?EIO=4&transport=polling&t={self.timestamp}"
        resp = self.session.get(url=url_polling)
        
        if resp.status_code != 200:
            raise Exception(f"Polling failed with status {resp.status_code}: {resp.text[:200]}")
            
        try:
            json_start = resp.text.find("{")
            if json_start == -1:
                raise Exception(f"No JSON found in polling response: {resp.text}")
            self.session_id: str = loads(resp.text[json_start:])["sid"]
        except Exception as e:
             raise Exception(f"Failed to parse session ID: {e}")
        
        self.is_request_finished: bool = True
        self.response_queue: List[Dict[str, Any]] = []

        # Handshake
        jwt_data = '40{"jwt":"anonymous-ask-user"}'
        handshake_url = f"https://www.perplexity.ai/socket.io/?EIO=4&transport=polling&t={self.timestamp}&sid={self.session_id}"
        handshake_resp = self.session.post(url=handshake_url, data=jwt_data)
        
        if handshake_resp.text != "OK":
             if handshake_resp.status_code != 200:
                 raise Exception(f"Handshake failed with status {handshake_resp.status_code}")
        
        print("[*] Upgrading to WebSocket...", file=sys.stderr)
        self.websocket: WebSocketApp = self._initialize_websocket()
        self.websocket_thread: Thread = Thread(target=self.websocket.run_forever)
        self.websocket_thread.daemon = True
        self.websocket_thread.start()
        
        while not (self.websocket.sock and self.websocket.sock.connected):
            sleep(0.1)
        print("[*] WebSocket connected.", file=sys.stderr)

    def _initialize_websocket(self) -> WebSocketApp:
        def on_open(ws: WebSocketApp) -> None:
            ws.send("2probe")
            ws.send("5")

        def on_message(ws: WebSocketApp, message: str) -> None:
            if message == "2":
                ws.send("3")
            elif not self.is_request_finished:
                try:
                    if message.startswith("42"):
                        message_data = loads(message[2:])
                        event_name = message_data[0]
                        content = message_data[1]
                        if isinstance(content, dict):
                            self.response_queue.append({"event": event_name, "data": content})
                        if event_name == "query_answered":
                            self.is_request_finished = True
                    elif message.startswith("43"):
                        raw_data = message[3:]
                        if raw_data.endswith("]"):
                            message_data = loads(raw_data)
                            if isinstance(message_data, list) and len(message_data) > 0:
                                self.response_queue.append({"event": "ack", "data": message_data[0]})
                                self.is_request_finished = True
                except:
                    pass

        # Build cookie string for WebSocket
        try:
            # curl_cffi cookies object - convert to dict-like structure
            cookie_items = []
            if hasattr(self.session.cookies, 'jar'):
                for cookie in self.session.cookies.jar:
                    cookie_items.append(f"{cookie.name}={cookie.value}")
            cookies_str = "; ".join(cookie_items) if cookie_items else ""
        except:
            cookies_str = ""
        
        # Proxy Configuration for WebSocket
        proxy_opts = {}
        if self.proxy_url:
            try:
                parsed = urlparse(self.proxy_url)
                proxy_opts["http_proxy_host"] = parsed.hostname
                proxy_opts["http_proxy_port"] = parsed.port
                if parsed.username and parsed.password:
                   proxy_opts["http_proxy_auth"] = (parsed.username, parsed.password)
            except Exception as e:
                print(f"[!] Invalid Proxy URL: {e}", file=sys.stderr)

        return WebSocketApp(
            url=f"wss://www.perplexity.ai/socket.io/?EIO=4&transport=websocket&sid={self.session_id}",
            header=self.request_headers,
            cookie=cookies_str,
            on_open=on_open,
            on_message=on_message,
            on_error=lambda ws, err: print(f"WebSocket error: {err}", file=sys.stderr),
            **proxy_opts
        )

    def generate_answer(self, query: str, model: str = "claude-3.5-sonnet") -> Generator[Dict[str, Any], None, None]:
        """
        Generate answer using specified model.
        Available models:
        - claude-3.5-sonnet (default)
        - gpt-4o
        - gpt-4-turbo
        """
        self.is_request_finished = False
        self.response_queue = []
        payload = [
            "perplexity_ask", 
            query, 
            {
                "frontend_session_id": str(uuid4()), 
                "language": "en-GB", 
                "timezone": "UTC", 
                "search_focus": "internet", 
                "frontend_uuid": str(uuid4()), 
                "mode": "search",
                "model": model  # Specify the model
            }
        ]
        self.websocket.send("421" + dumps(payload))
        
        start_time: float = time()
        while (not self.is_request_finished) or len(self.response_queue) != 0:
            if time() - start_time > 180:
                self.is_request_finished = True
                yield {"error": "Timed out after 180s."}
                break
            if len(self.response_queue) != 0:
                yield self.response_queue.pop(0)
            sleep(0.05)

    def ask(self, query: str, model: str = "claude-3.5-sonnet") -> str:
        """
        Ask a question using specified model (default: Claude Sonnet 4.5)
        """
        print(f"\n[QUERY]: {query}")
        print(f"[MODEL]: {model}")
        print("-" * 60)
        answers = self.generate_answer(query, model=model)
        accumulated_text = ""
        
        print("[RESPONSE]", flush=True)
        for msg in answers:
            if "error" in msg:
                print(f"\n[!] {msg['error']}")
                break
                
            data = msg.get("data", {})
            if not isinstance(data, dict): 
                continue
            
            # Extract text from markdown blocks - Perplexity sends cumulative blocks
            if "blocks" in data:
                current_text = ""
                for block in data["blocks"]:
                    if isinstance(block, dict) and "markdown_block" in block:
                        markdown = block["markdown_block"]
                        if "chunks" in markdown and isinstance(markdown["chunks"], list):
                            for chunk in markdown["chunks"]:
                                if isinstance(chunk, str):
                                    current_text += chunk
                
                # Only print and accumulate new text
                if len(current_text) > len(accumulated_text):
                    new_stuff = current_text[len(accumulated_text):]
                    print(new_stuff, end="", flush=True)
                    accumulated_text = current_text
            
        print("\n" + "="*60)
        return accumulated_text

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default="Hello!")
    parser.add_argument("--token")
    args = parser.parse_args()
    token = args.token or os.environ.get("PERPLEXITY_TOKEN")
    
    print("\n" + "="*60)
    print(" PERPLEXITY AI CLIENT ".center(60, "="))
    print("="*60)
    
    try:
        client = Perplexity(token=token)
        client.ask(args.query)
        print("Done.")
    except Exception as e:
        print(f"\n[!] Failed: {e}", file=sys.stderr)
        sys.exit(1)
