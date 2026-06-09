import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.db")

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._init()

    def _init(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            role TEXT,
            content TEXT,
            score INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            content TEXT,
            source TEXT,
            used_count INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS bad_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT UNIQUE,
            reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS long_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT UNIQUE,
            summary TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS evolution_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT,
            detail TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS dynamic_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            description TEXT,
            response_template TEXT,
            status TEXT DEFAULT 'pending',
            proposed_by TEXT,
            approved_by TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS command_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            description TEXT,
            response_template TEXT,
            trigger_context TEXT,
            status TEXT DEFAULT 'pending',
            proposed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)
        self.conn.commit()

    # --- 会話履歴 ---
    def add_message(self, user_id, role, content, score=0):
        self.conn.execute(
            "INSERT INTO history(user_id,role,content,score) VALUES(?,?,?,?)",
            (user_id, role, content, score)
        )
        self.conn.commit()

    def get_history(self, user_id, limit=12):
        rows = self.conn.execute(
            "SELECT role,content FROM history WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        rows.reverse()
        return [{"role": r[0], "content": r[1]} for r in rows]

    def update_score(self, user_id, delta):
        row = self.conn.execute(
            "SELECT id FROM history WHERE user_id=? AND role='assistant' ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        if row:
            self.conn.execute("UPDATE history SET score=score+? WHERE id=?", (delta, row[0]))
            self.conn.commit()

    def clear_history(self, user_id):
        self.conn.execute("DELETE FROM history WHERE user_id=?", (user_id,))
        self.conn.commit()

    # --- 知識ベース ---
    def add_knowledge(self, query, content, source=""):
        self.conn.execute(
            "INSERT INTO knowledge(query,content,source) VALUES(?,?,?)",
            (query, content[:1000], source)
        )
        self.conn.commit()

    def search_knowledge(self, query, limit=3):
        words = query.split()[:4]
        like = "%" + "%".join(words) + "%"
        rows = self.conn.execute(
            "SELECT id,query,content FROM knowledge WHERE query LIKE ? OR content LIKE ? ORDER BY used_count DESC LIMIT ?",
            (like, like, limit)
        ).fetchall()
        for r in rows:
            self.conn.execute("UPDATE knowledge SET used_count=used_count+1 WHERE id=?", (r[0],))
        self.conn.commit()
        return [(r[1], r[2]) for r in rows]

    # --- 悪パターン ---
    def add_bad_pattern(self, pattern, reason=""):
        try:
            self.conn.execute(
                "INSERT INTO bad_patterns(pattern,reason) VALUES(?,?)", (pattern[:200], reason)
            )
            self.conn.commit()
        except:
            pass

    def get_bad_patterns(self):
        return [r[0] for r in self.conn.execute("SELECT pattern FROM bad_patterns").fetchall()]

    # --- 長期記憶 ---
    def get_long_memory(self, user_id):
        row = self.conn.execute(
            "SELECT summary FROM long_memory WHERE user_id=?", (user_id,)
        ).fetchone()
        return row[0] if row else ""

    def set_long_memory(self, user_id, summary):
        self.conn.execute(
            "INSERT INTO long_memory(user_id,summary) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET summary=?,updated_at=CURRENT_TIMESTAMP",
            (user_id, summary, summary)
        )
        self.conn.commit()

    # --- 進化ログ ---
    def log_evolution(self, event, detail=""):
        self.conn.execute(
            "INSERT INTO evolution_log(event,detail) VALUES(?,?)", (event, detail[:500])
        )
        self.conn.commit()

    def get_evolution_log(self, limit=10):
        return self.conn.execute(
            "SELECT event,detail,created_at FROM evolution_log ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()

    # --- 動的コマンド提案 ---
    def propose_command(self, name, description, response_template, trigger_context=""):
        # 同名の提案が既にpendingなら重複しない
        exists = self.conn.execute(
            "SELECT id FROM command_proposals WHERE name=? AND status='pending'", (name,)
        ).fetchone()
        if exists:
            return False
        # 承認済みに同名があれば重複しない
        exists2 = self.conn.execute(
            "SELECT id FROM dynamic_commands WHERE name=?", (name,)
        ).fetchone()
        if exists2:
            return False
        self.conn.execute(
            "INSERT INTO command_proposals(name,description,response_template,trigger_context) VALUES(?,?,?,?)",
            (name, description[:100], response_template[:500], trigger_context[:200])
        )
        self.conn.commit()
        return True

    def get_pending_proposals(self):
        return self.conn.execute(
            "SELECT id,name,description,response_template,trigger_context FROM command_proposals WHERE status='pending' ORDER BY proposed_at DESC"
        ).fetchall()

    def approve_proposal(self, proposal_id, approved_by):
        row = self.conn.execute(
            "SELECT name,description,response_template FROM command_proposals WHERE id=? AND status='pending'",
            (proposal_id,)
        ).fetchone()
        if not row:
            return None
        name, desc, tmpl = row
        try:
            self.conn.execute(
                "INSERT INTO dynamic_commands(name,description,response_template,status,approved_by) VALUES(?,?,?,'approved',?)",
                (name, desc, tmpl, approved_by)
            )
        except:
            return None
        self.conn.execute(
            "UPDATE command_proposals SET status='approved' WHERE id=?", (proposal_id,)
        )
        self.conn.commit()
        return {"name": name, "description": desc, "response_template": tmpl}

    def reject_proposal(self, proposal_id):
        self.conn.execute(
            "UPDATE command_proposals SET status='rejected' WHERE id=?", (proposal_id,)
        )
        self.conn.commit()

    def get_approved_commands(self):
        return self.conn.execute(
            "SELECT name,description,response_template FROM dynamic_commands WHERE status='approved'"
        ).fetchall()

    def delete_dynamic_command(self, name):
        self.conn.execute("DELETE FROM dynamic_commands WHERE name=?", (name,))
        self.conn.commit()

    # --- 統計 ---
    def get_stats(self, user_id):
        total     = self.conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        user_total= self.conn.execute("SELECT COUNT(*) FROM history WHERE user_id=?", (user_id,)).fetchone()[0]
        knowledge = self.conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        bad       = self.conn.execute("SELECT COUNT(*) FROM bad_patterns").fetchone()[0]
        dyn_cmds  = self.conn.execute("SELECT COUNT(*) FROM dynamic_commands WHERE status='approved'").fetchone()[0]
        pending   = self.conn.execute("SELECT COUNT(*) FROM command_proposals WHERE status='pending'").fetchone()[0]
        avg_score = self.conn.execute(
            "SELECT AVG(score) FROM history WHERE role='assistant' AND score!=0"
        ).fetchone()[0] or 0
        return {
            "total": total, "user_total": user_total,
            "knowledge": knowledge, "bad_patterns": bad,
            "dynamic_commands": dyn_cmds, "pending_proposals": pending,
            "avg_score": round(avg_score, 2),
      }
