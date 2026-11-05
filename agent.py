# -*- coding: utf-8 -*-
# وكيل الكمبيوتر — Tab: يتحكم في الويندوز بأوامر عربية عبر Gemini
import os, sys, json, time, re, base64, io, threading, subprocess, webbrowser, traceback
import tkinter as tk
from tkinter import scrolledtext
import requests, pyautogui, pyperclip
from PIL import ImageGrab, ImageDraw

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.3
KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not KEY and os.path.exists(r"C:\agent\key.txt"):
    KEY = open(r"C:\agent\key.txt", encoding="utf-8").read().strip()
MODELS = [m for m in os.environ.get("AGENT_MODELS", "gemini-3.5-flash,gemini-3-flash-preview,gemini-3.8-flash,gemini-flash-latest").split(",") if m]
MAX_STEPS = 40

SYSTEM = """You are an AI agent operating a real Windows PC (mouse + keyboard) for an Arabic-speaking user.
Each turn you get the user's goal (usually in Arabic), the history of your previous actions, and a fresh screenshot of the whole screen.
The small window titled "وكيل الكمبيوتر" docked in the screen corner is YOU (your chat window) — never click it or close it.
Reply with ONLY one JSON object, no markdown:
{"thought": "<short reasoning, Arabic>", "action": "<one action>", ...args}
Actions:
- {"action":"click","x":X,"y":Y}            left click. X,Y are coordinates on the screenshot normalized 0-1000 (0,0 top-left; 1000,1000 bottom-right)
- {"action":"double_click","x":X,"y":Y}
- {"action":"right_click","x":X,"y":Y}
- {"action":"type","x":X,"y":Y,"text":"..."}  clicks the field at X,Y first (give them!), then types text (any language); add "enter":true to press Enter after
- {"action":"key","keys":"ctrl+l"}          key or combo: enter, tab, esc, backspace, ctrl+a, alt+f4, win, pagedown...
- {"action":"scroll","x":X,"y":Y,"amount":-5} negative = down
- {"action":"open_url","url":"https://..."}  opens the URL in the default browser (fastest way to reach a website)
- {"action":"run","cmd":"..."}              runs a PowerShell command (e.g. to open apps, download files with Invoke-WebRequest, list files). Output is returned to you.
- {"action":"wait","seconds":3}
- {"action":"ask","text":"<question in Arabic>"}   when you need info only the user has (password, code, choice). Then stop.
- {"action":"done","text":"<short summary in Sudanese/simple Arabic of what you did>"}
Rules: one action per reply. Prefer open_url / keyboard shortcuts over hunting with the mouse. After typing in a search box press Enter. Verify on the screenshot that the previous action worked before moving on. Downloads go to C:\\Users\\<user>\\Downloads unless told otherwise. Never enter passwords you were not given. Be efficient."""

def shot():
    img = ImageGrab.grab(all_screens=True)
    W, H = img.size
    sm = img.copy(); sm.thumbnail((1366, 1366))
    b = io.BytesIO(); sm.convert("RGB").save(b, "JPEG", quality=70)
    return base64.b64encode(b.getvalue()).decode(), W, H

def ask_gemini(goal, history, b64):
    hist = "\n".join(history[-15:]) or "(none yet)"
    body = {"system_instruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [
                {"text": f"GOAL: {goal}\n\nPREVIOUS ACTIONS:\n{hist}\n\nHere is the current screenshot. Next action JSON:"},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}}]}],
            "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json"}}
    last = None
    for attempt in range(6):
        for m in MODELS:
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                                  headers={"x-goog-api-key": KEY}, json=body, timeout=90)
                if r.status_code == 200:
                    j = r.json()
                    txt = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"] if not p.get("thought"))
                    mm = re.search(r"\{.*\}", txt, re.S)
                    return json.loads(mm.group(0)), m
                last = f"{m}: {r.status_code} {r.text[:200]}"
            except Exception as e:
                last = f"{m}: {e}"
        time.sleep(4 + attempt * 3)
    raise RuntimeError(last)

def px(a, W, H):
    return int(float(a["x"]) / 1000 * W), int(float(a["y"]) / 1000 * H)

def execute(a, W, H):
    t = a.get("action")
    if t in ("click", "double_click", "right_click"):
        x, y = px(a, W, H); pyautogui.moveTo(x, y, duration=0.2)
        {"click": pyautogui.click, "double_click": pyautogui.doubleClick, "right_click": pyautogui.rightClick}[t](x, y)
        return f"{t} at {x},{y}"
    if t == "type":
        if "x" in a and "y" in a:
            x, y = px(a, W, H); pyautogui.click(x, y); time.sleep(0.4)
        pyperclip.copy(str(a.get("text", ""))); time.sleep(0.2); pyautogui.hotkey("ctrl", "v")
        if a.get("enter"): time.sleep(0.3); pyautogui.press("enter")
        return "typed"
    if t == "key":
        ks = [k.strip().lower() for k in str(a.get("keys", "")).replace(" ", "").split("+") if k.strip()]
        ks = ["win" if k in ("windows", "super", "meta") else k for k in ks]
        pyautogui.hotkey(*ks) if len(ks) > 1 else pyautogui.press(ks[0])
        return "pressed " + "+".join(ks)
    if t == "scroll":
        if "x" in a: pyautogui.moveTo(*px(a, W, H))
        pyautogui.scroll(int(a.get("amount", -5)) * 120); return "scrolled"
    if t == "open_url":
        os.startfile(a["url"]); time.sleep(4); return "opened " + a["url"]
    if t == "run":
        p = subprocess.run(["powershell", "-NoProfile", "-Command", a["cmd"]], capture_output=True, text=True, timeout=600)
        return "output: " + (p.stdout + p.stderr)[-600:]
    if t == "wait":
        time.sleep(min(float(a.get("seconds", 2)), 30)); return "waited"
    return "unknown action"

class App:
    def __init__(self, root):
        self.root = root; self.running = False; self.stop_flag = False
        root.title("وكيل الكمبيوتر"); root.attributes("-topmost", True)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = 380, 330
        root.geometry(f"{w}x{h}+{sw-w-10}+{sh-h-60}")
        tk.Label(root, text="اكتب أمرك بالعربي واضغط تنفيذ", font=("Segoe UI", 11, "bold")).pack(pady=3)
        self.e = tk.Text(root, height=3, font=("Segoe UI", 12), wrap="word"); self.e.pack(fill="x", padx=6)
        self.e.tag_configure("r", justify="right")
        fr = tk.Frame(root); fr.pack(pady=4)
        tk.Button(fr, text="تنفيذ ▶", font=("Segoe UI", 11, "bold"), bg="#1a7f37", fg="white", command=self.go).pack(side="right", padx=4)
        tk.Button(fr, text="إيقاف ■", font=("Segoe UI", 11), command=self.stop).pack(side="right", padx=4)
        self.log = scrolledtext.ScrolledText(root, height=9, font=("Segoe UI", 10), wrap="word"); self.log.pack(fill="both", expand=True, padx=6, pady=4)
        self.log.tag_configure("r", justify="right")
        self.say("جاهز ✅ مثلاً: افتح يوتيوب وابحث عن سيادة" if KEY else "⚠️ مفتاح Gemini غير موجود")

    def say(self, s):
        def f():
            self.log.insert("end", s + "\n", "r"); self.log.see("end")
        self.root.after(0, f)

    def stop(self): self.stop_flag = True; self.say("⏹ جاري الإيقاف...")

    def go(self):
        goal = self.e.get("1.0", "end").strip()
        if not goal or self.running: return
        self.running = True; self.stop_flag = False
        threading.Thread(target=self.loop, args=(goal,), daemon=True).start()

    def loop(self, goal):
        hist = []; self.say("🎯 " + goal)
        try:
            for i in range(MAX_STEPS):
                if self.stop_flag: break
                b64, W, H = shot()
                a, m = ask_gemini(goal, hist, b64)
                self.say(f"{i+1}. {a.get('thought','')}")
                if a.get("action") in ("done", "ask"):
                    self.say(("✅ " if a["action"] == "done" else "❓ ") + str(a.get("text", ""))); break
                res = execute(a, W, H)
                hist.append(f"{i+1}. {json.dumps(a, ensure_ascii=False)} -> {res}")
                with open(os.path.join(os.environ.get("TEMP", "."), "agent_log.txt"), "a", encoding="utf-8") as f:
                    f.write(hist[-1] + "\n")
                time.sleep(1.2)
            else:
                self.say("⚠️ وصلت لحد الخطوات، جرّب تقسم الأمر")
        except Exception as e:
            self.say("❌ خطأ: " + str(e)[:300])
        self.running = False

if __name__ == "__main__":
    if len(sys.argv) > 1:   # headless test:  python agent.py "goal"
        class Fake:
            def after(self, _, f): f()
        app = App.__new__(App); app.root = Fake(); app.stop_flag = False
        app.say = lambda s: print(s, flush=True)
        app.loop(" ".join(sys.argv[1:])); sys.exit(0)
    root = tk.Tk(); App(root); root.mainloop()
