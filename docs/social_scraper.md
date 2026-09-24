# Social Scraper — Backup toàn bộ Facebook / Instagram / TikTok cho AI Agent

`agent-reach-social` cào **một page/profile/user URL** → trả về **mọi dữ liệu nền tảng
trả ra** (posts, videos, lives, events, insights…), lưu backup có cấu trúc
(manifest + sha256 + raw payload nguyên văn), tải media về local, và phát qua
**MCP server** để mọi AI agent (Claude, Cursor, OpenCode, OpenClaw…) đọc trực tiếp.

Không cần API key — chạy trên trình duyệt Playwright với **phiên đăng nhập của chính bạn**.

---

## 1. Cài đặt

```bash
# dependency (playwright + mcp)
pip install -e '.[social]'

# trình duyệt (chỉ cần 1 lần)
playwright install chromium

# kiểm tra
agent-reach-social status
```

Không có `agent-reach-social` trong PATH (pipx/virtualenv cũ)? Dùng:

```bash
python -m agent_reach.social_scraper status
```

---

## 2. Đăng nhập (2 cách)

Nền tảng này cần đăng nhập. Chọn 1 trong 2 cách:

### Cách A — đăng nhập tương tác (khuyến nghị)

```bash
agent-reach-social login facebook      # → https://www.facebook.com/login
agent-reach-social login instagram     # → https://www.instagram.com/accounts/login/
agent-reach-social login tiktok        # → https://www.tiktok.com/login
```

Trình duyệt hiện ra → **bạn tự đăng nhập** → công cụ poll cookie phiên
(Facebook: `c_user`+`xs`, Instagram: `sessionid`, TikTok: `sessionid`/`sid_guard`)
→ lưu storage_state vào `~/.agent-reach/social-sessions/<platform>.json` (chmod 600).

### Cách B — import Cookie (không mở browser, hợp server)

Dùng extension **Cookie-Editor** export JSON (xem `docs/cookie-export.md`), rồi trỏ env:

```bash
export SOCIAL_FB_COOKIES=/path/to/facebook-cookies.json
export SOCIAL_IG_COOKIES=/path/to/instagram-cookies.json
export SOCIAL_TIKTOK_COOKIES=/path/to/tiktok-cookies.json
```

Hỗ trợ 3 định dạng: Cookie-Editor JSON (list), Playwright storage_state JSON, Netscape.txt.

---

## 3. CLI

```bash
# Cào toàn bộ một page/profile (posts + videos + lives + events + media)
agent-reach-social scrape https://www.instagram.com/nasa/

# Chọn loại nội dung, giới hạn, không tải media, xem browser đang chạy
agent-reach-social scrape https://www.facebook.com/nasa \
    --kinds posts,videos,events --max-items 50 --max-scroll 10 --headed

# Output JSON cho agent/script
agent-reach-social scrape https://www.tiktok.com/@nasa --json

# Tra backup đã có
agent-reach-social list --platform instagram --handle nasa
agent-reach-social list --targets                 # danh sách target + tổng số
agent-reach-social search "launches" --kind posts
agent-reach-social show <resource-id> --raw       # kèm raw payload gốc
agent-reach-social download --platform instagram --handle nasa   # tải media còn thiếu
agent-reach-social status --json                  # thống kê + chẩn đoán dependency
```

`kinds`: `posts, videos, lives, events, stories, comments, profile, all` (mặc định `all`).

> Ý nghĩa kind: Facebook/Instagram — post **có video** match yêu cầu `videos`;
> TikTok — video **cũng được tính là post**. Nói "lấy hết" = để mặc định `all`.

---

## 4. Cấu trúc backup

Mỗi lần scrape = 1 session riêng, không ghi đè:

```
~/.agent-reach/social-backups/            # đổi bằng $SOCIAL_BACKUP_DIR hoặc --backup-dir
└── instagram/nasa/2026-09-24T12-00-00Z/
    ├── manifest.json        # inventory: counts, sha256 từng file, media stats, errors
    ├── profile.json         # hồ sơ target
    ├── index.jsonl          # 1 resource = 1 dòng JSON → query/lookup/search
    ├── raw/0001_posts.json  # payload nền tảng trả về, LƯU NGUYÊN VĂN
    ├── raw/0002_videos.json
    └── media/…              # ảnh/video đã tải (sha256 ghi trong asset)
```

- **Raw payload giữ verbatim** — metadata (bucket, source_url, sha256) nằm trong
  `manifest.json`, không chèn vào file JSON gốc.
- Path segment được sanitize chống traversal; file ghi atomic (temp + rename).
- Trùng file đã có (cùng sha256) → skip, không tải lại.

---

## 5. MCP server — cấp tài nguyên cho AI agent

### Tools (8)

| Tool | Việc làm |
|------|----------|
| `social_scrape` | Cào 1 URL → backup + tải media, trả resources đã chuẩn hóa |
| `social_list_targets` | Liệt kê các target đã backup + tổng số |
| `social_list_resources` | Danh sách resource (phân trang, filter platform/handle/kind) |
| `social_get_resource` | 1 resource theo id (kèm media + raw back-reference) |
| `social_read_raw` | **Payload gốc đúng bằng cái nền tảng trả về** |
| `social_search` | Tìm full-text trong backup (text/author/url) |
| `social_download_media` | (Tải lại) media theo id hoặc theo target |
| `social_status` | Thống kê backup + chẩn đoán dependency |

### Resources

Mọi resource cũng là URI đọc được: `social://<platform>/<handle>/<stamp>/<id>` —
hoặc `social://…/profile` cho hồ sơ target.

### Cấu hình (Claude Desktop / Cursor / mọi MCP client)

```json
{
  "mcpServers": {
    "social-reach": {
      "command": "agent-reach-social",
      "args": ["serve-mcp"]
    }
  }
}
```

Không có entrypoint? Dùng module:

```json
{
  "mcpServers": {
    "social-reach": {
      "command": "python",
      "args": ["-m", "agent_reach.social_scraper.mcp.server"]
    }
  }
}
```

Server tự đọc env (`SOCIAL_BACKUP_DIR`…) — client config cũng nên truyền env nếu đổi backup root.

**Tương thích SDK:** server detect `mcp` đang cài là đời mới (callback
`on_list_tools=…` của MCP ≥2.x) hay cũ (decorator `@server.list_tools()` của 1.x)
và tự chọn cách đăng ký — cả hai đều chạy được.

Agent thao tác mẫu: *"cào https://www.instagram.com/nasa/ rồi liệt kê posts có
lượt like > 1000, cho tôi xem raw payload của bài đầu tiên"*.

---

## 6. Biến môi trường

| Env | Mặc định | Ý nghĩa |
|-----|----------|---------|
| `SOCIAL_BACKUP_DIR` | `~/.agent-reach/social-backups` | Gốc backup |
| `SOCIAL_SESSION_DIR` | `~/.agent-reach/social-sessions` | Phiên đăng nhập |
| `SOCIAL_HEADLESS` | `1` | `0` = thấy cửa sổ trình duyệt |
| `SOCIAL_MAX_SCROLL` | `15` | Số vòng cuộn trang |
| `SOCIAL_MAX_ITEMS` | `100` | Giới hạn item / kind |
| `SOCIAL_TIMEOUT_MS` | `45000` | Timeout điều hướng |
| `SOCIAL_DOWNLOAD_CONCURRENCY` | `4` | Song song tải media |
| `SOCIAL_DOWNLOAD_RETRIES` / `_TIMEOUT` | `3` / `60` | Retry + timeout tải |
| `SOCIAL_USER_AGENT` | Chrome 126 desktop | UA trình duyệt |
| `SOCIAL_FB_COOKIES` / `SOCIAL_IG_COOKIES` / `SOCIAL_TIKTOK_COOKIES` | — | File cookie mỗi nền tảng |

Toàn bộ có trong `.env.example`.

---

## 7. Tuân thủ & an toàn (đọc trước khi chạy với quy mô lớn)

- **Chỉ backup dữ liệu bạn có quyền truy cập** — dùng chính tài khoản (hoặc tài
  khoản phụ), tôn trọng ToS nền tảng và quy định bản quyền nội dung.
- **Pacing lịch sự**: tool đã tự nghỉ 0.6–1.4s giữa các vòng cuộn và có
  `--max-items` / `--max-scroll`. Đừng đẩy lên cao — nhanh = khóa checkpoint /
  429 / yêu cầu xác minh.
- **Cookie = toàn quyền tài khoản**: nằm local (chmod 600), không commit, không
  paste lên nơi công cộng. Dùng tài khoản phụ thay vì tài khoản chính.
- **Không đăng nhập thay người dùng**: luồng `login` mở trình duyệt để BẠN gõ
  mật khẩu; tool chỉ chờ cookie xuất hiện.
- Lỗi in ra đều được scrub credential (`user:pass@host`, `?token=…` → `***`).

---

## 8. Sự cố thường gặp

| Triệu chứng | Nguyên nhân → Cách sửa |
|-------------|------------------------|
| `Playwright is required…` | `pip install -e '.[social]'` rồi `playwright install chromium` |
| `Login … timed out` | Chưa đăng nhập kịp trong cửa sổ → chạy lại `login <platform>`, tăng `--timeout` |
| `Login … timed out` nhưng đã gõ đúng mật khẩu | Cookie phiên chưa set → đăng nhập lại, kiểm tra không bật extension chặn cookie |
| `No scraper for '…'` | URL không phải page/profile của 3 nền tảng (vd. có `/reel/` trỏ trung gian) → dùng URL profile chuẩn |
| `MCP not installed` | `pip install -e '.[social]'` |
| Cào ra 0 resource | Session hết hạn / bị chặn login → `login` lại; thử `--headed` xem trình duyệt đang thấy gì |
| Media `failed` | URL CDN hết hạn → `agent-reach-social download --id <id>` tải lại ngay sau scrape |

---

## 9. Kiểm chứng

```bash
pytest tests/social_scraper/ -q    # unit + integration (không cần mạng/browser)
ruff check agent_reach/social_scraper tests/social_scraper
mypy agent_reach/social_scraper
```
