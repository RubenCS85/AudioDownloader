from __future__ import annotations

import tkinter as tk
from audiodl.ui.tkui.app import AudioDLTkApp

def main() -> int:
    root = tk.Tk()
    app = AudioDLTkApp(root)
    app.pack(fill="both", expand=True)
    root.mainloop()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
