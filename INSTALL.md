# Installation

NWlogReader requires Python 3.9+ and runs on port 8999.

---

## Linux

```bash
git clone https://github.com/bmar/nwlogreader.git
cd nwlogreader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open [http://localhost:8999](http://localhost:8999).

---

## macOS

If using Homebrew Python, ensure you are using Python 3.9+:

```bash
brew install python
```

Then follow the same steps as Linux:

```bash
git clone https://github.com/bmar/nwlogreader.git
cd nwlogreader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open [http://localhost:8999](http://localhost:8999).

---

## Windows

```cmd
git clone https://github.com/bmar/nwlogreader.git
cd nwlogreader
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open [http://localhost:8999](http://localhost:8999).

> Note: If Windows Firewall prompts for network access, allow it (the app binds to `0.0.0.0:8999`).

---

## Verify

After starting, the app opens a browser automatically. If not, navigate to:

```
http://localhost:8999
```

---

## Troubleshooting

**Port 8999 already in use:**

```bash
# Linux/macOS
sudo lsof -i :8999
kill -9 <PID>

# Windows
netstat -ano | findstr :8999
taskkill /PID <PID> /F
```

**Wrong Python version:**

```bash
python3 --version  # must be 3.9+
```

**pip install fails:**

```bash
pip install --upgrade pip
pip install -r requirements.txt
```
