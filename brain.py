import httpx, asyncio, json, re
from db import Database
from search import web_search, format_results

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen2.5:1.5b"

CORE_IDENTITY = """あなたは自己進化する学習型AIアシスタントです。
会話を通じて日々賢くなり、ユーザーの役に立つことを目指します。
日本語で自然に話してください。絵文字は使わず、簡潔に答えてください。
有害・違法・プライバシー侵害な内容には応じないでください。"""

SEARCH_TRIGGERS = ["最新","今日","ニュース","天気","いつ","誰が","調べて",
                   "検索","最近","現在","今","速報","情報","what","who","when","news","latest"]

TOXIC_PATTERNS = ["爆弾","殺し","自殺","薬物","ハッキング","不正アクセス",
                  "クレジットカード番号","個人情報盗","パスワード盗"]

def is_toxic(text: str) -> bool:
    return any(p in text for p in TOXIC_PATTERNS)

def needs_search(text: str) -> bool:
    return any(kw in text.lower() for kw in SEARCH_TRIGGERS)


class Brain:
    def __init__(self):
        self.db = Database()
        self._bad_cache = set(self.db.get_bad_patterns())

    async def think(self, user_id, username, text, force_search=False):
        if is_toxic(text):
            self.db.log_evolution("toxic_blocked", text[:100])
            return "その内容には応答できません。"

        history   = self.db.get_history(user_id, limit=12)
        long_mem  = self.db.get_long_memory(user_id)
        dyn_cmds  = self.db.get_approved_commands()

        # 検索
        search_ctx = ""
        if force_search or needs_search(text):
            results = web_search(text, max_results=3)
            if results:
                search_ctx = format_results(results)
                if not is_toxic(search_ctx):
                    self.db.add_knowledge(text, search_ctx)
                    self.db.log_evolution("web_learned", f"query={text[:60]}")
        if not search_ctx:
            known = self.db.search_knowledge(text)
            if known:
                search_ctx = "\n".join(k[1] for k in known[:2])

        # 動的コマンド一覧をシステムに渡す
        dyn_cmd_hint = ""
        if dyn_cmds:
            lines = [f"/{r[0]}: {r[1]}" for r in dyn_cmds]
            dyn_cmd_hint = "\n[追加済みコマンド]\n" + "\n".join(lines)

        system = CORE_IDENTITY
        if long_mem:
            system += f"\n\n[このユーザーの長期記憶]\n{long_mem}"
        if search_ctx:
            system += f"\n\n[参考情報]\n{search_ctx}"
        if dyn_cmd_hint:
            system += dyn_cmd_hint

        messages = [{"role": "system", "content": system}] + history
        messages.append({"role": "user", "content": text})

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                res = await client.post(OLLAMA_URL, json={
                    "model": MODEL, "messages": messages, "stream": False,
                    "options": {"temperature": 0.7, "num_ctx": 2048},
                })
                reply = res.json()["message"]["content"].strip()
        except Exception as e:
            print(f"[brain] {e}")
            return "少し調子が悪いです。もう一度試してください。"

        if is_toxic(reply):
            self.db.add_bad_pattern(text[:100], "toxic_reply")
            self._bad_cache.add(text[:100])
            self.db.log_evolution("toxic_reply_filtered", reply[:100])
            return "うまく答えられませんでした。別の聞き方をしてみてください。"

        self.db.add_message(user_id, "user", text)
        self.db.add_message(user_id, "assistant", reply)

        # 20往復ごとに長期記憶を更新
        if len(history) > 0 and len(history) % 20 == 0:
            asyncio.create_task(self._update_long_memory(user_id))

        # 会話からコマンド提案を非同期で検討（10往復ごと）
        if len(history) > 0 and len(history) % 10 == 0:
            asyncio.create_task(self._consider_new_command(user_id, history + [{"role":"user","content":text}, {"role":"assistant","content":reply}]))

        return reply

    async def run_dynamic_command(self, name: str, user_input: str) -> str | None:
        """動的コマンドを実行（テンプレートをOllamaで解釈して返答）"""
        cmds = {r[0]: (r[1], r[2]) for r in self.db.get_approved_commands()}
        if name not in cmds:
            return None
        desc, template = cmds[name]
        prompt = f"コマンド「/{name}」({desc})が呼ばれました。\nテンプレート: {template}\nユーザー入力: {user_input}\n上記をもとに返答してください。"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(OLLAMA_URL, json={
                    "model": MODEL,
                    "messages": [{"role":"user","content":prompt}],
                    "stream": False,
                    "options": {"temperature": 0.5, "num_ctx": 1024},
                })
                return res.json()["message"]["content"].strip()
        except:
            return "コマンド実行中にエラーが発生しました。"

    async def _consider_new_command(self, user_id: str, history: list):
        """会話を分析して新コマンドを提案するか検討する"""
        conv = "\n".join(f"{m['role']}: {m['content']}" for m in history[-10:])
        prompt = f"""以下の会話を分析して、Discordボットに追加すると便利なコマンドが1つあれば提案してください。
なければ "NONE" とだけ返してください。
提案する場合は必ず以下のJSON形式のみで返してください（他の文章は不要）:
{{"name":"コマンド名（英小文字、スペースなし）","description":"説明（日本語20文字以内）","response_template":"このコマンドが呼ばれたときの返答テンプレート（日本語100文字以内）","trigger_context":"なぜ提案するか（日本語50文字以内）"}}

会話:
{conv}"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(OLLAMA_URL, json={
                    "model": MODEL,
                    "messages": [{"role":"user","content":prompt}],
                    "stream": False,
                    "options": {"temperature": 0.4, "num_ctx": 1024},
                })
                raw = res.json()["message"]["content"].strip()

            if raw.upper().startswith("NONE") or raw == "":
                return

            # JSONを抽出
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())

            name = re.sub(r'[^a-z0-9_]', '', data.get("name",""))
            desc = data.get("description","")[:100]
            tmpl = data.get("response_template","")[:500]
            ctx  = data.get("trigger_context","")[:200]

            if not name or not desc or not tmpl:
                return

            # 禁止ワードチェック
            if is_toxic(desc) or is_toxic(tmpl):
                return

            added = self.db.propose_command(name, desc, tmpl, ctx)
            if added:
                self.db.log_evolution("command_proposed", f"/{name}: {desc}")

        except Exception as e:
            print(f"[consider_command] {e}")

    async def _update_long_memory(self, user_id: str):
        history = self.db.get_history(user_id, limit=30)
        if len(history) < 5:
            return
        conv = "\n".join(f"{m['role']}: {m['content']}" for m in history)
        prompt = f"以下の会話からユーザーの傾向・好み・重要情報を3〜5行で箇条書きにまとめてください。日本語で。\n\n{conv}"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(OLLAMA_URL, json={
                    "model": MODEL,
                    "messages": [{"role":"user","content":prompt}],
                    "stream": False,
                    "options": {"temperature": 0.3, "num_ctx": 2048},
                })
                summary = res.json()["message"]["content"].strip()
            if summary and not is_toxic(summary):
                self.db.set_long_memory(user_id, summary)
                self.db.log_evolution("long_memory_updated", f"user={user_id}")
        except Exception as e:
            print(f"[long_memory] {e}")

    def feedback(self, user_id, positive: bool):
        delta = 1 if positive else -1
        self.db.update_score(user_id, delta)
        if not positive:
            history = self.db.get_history(user_id, limit=2)
            if history:
                self.db.log_evolution("negative_feedback", history[-1]["content"][:100])
        self.db.log_evolution("feedback", "positive" if positive else "negative")
