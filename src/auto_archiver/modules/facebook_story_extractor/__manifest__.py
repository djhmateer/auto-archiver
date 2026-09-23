{
    "name": "Facebook Story Extractor",
    "type": ["extractor"],
    "entry_point": "facebook_story_extractor::FacebookStoryExtractor",
    "requires_setup": True,
    "dependencies": {
        "python": ["loguru", "selenium", "pytesseract", "PIL"],
    },
    "configs": {
        "width": {"default": 1280, "type": "int", "help": "width of the browser window used to load the story"},
        "height": {"default": 1024, "type": "int", "help": "height of the browser window used to load the story"},
        "timeout": {"default": 60, "type": "int", "help": "timeout in seconds for loading the story page"},
        "sleep_before_capture": {
            "default": 6,
            "type": "int",
            "help": "seconds to wait after navigation for the story (image/video) to finish rendering before reading the page/taking a screenshot",
        },
        "http_proxy": {
            "default": "",
            "help": "http proxy to use for the webdriver, eg http://proxy-user:password@proxy-ip:port",
        },
        "stop_on_suspected_block": {
            "default": True,
            "type": "bool",
            "help": "if true, aborts and fails the item (rather than just warning) when OCR detects Facebook 'automated behaviour'/login-wall phrases on the page - protects the logged-in account from further automated requests once it's been flagged",
        },
    },
    "description": """
Archives Facebook Stories (`facebook.com/stories/<id>`), which are not supported by the generic yt-dlp
Facebook drop-in since they are heavily JS-rendered, ephemeral (~24h), and normally require an
authenticated session even for otherwise-public accounts.

Requires a logged-in Facebook session's cookies, configured via the standard `authentication` block for
the `facebook.com` domain in the orchestration config - use a dedicated login/cookies file for this,
separate from any cookies used for other Facebook archiving, so a block on one doesn't affect the other.

### Features
- Loads the story in a real browser (via the shared `Webdriver` helper) using the configured cookies.
- Best-effort extraction of the raw media (image/video) URL, from embedded page JSON and/or the
  rendered DOM - Facebook's story markup/schema is undocumented and may need adjusting after testing
  against a real, logged-in story.
- Captures a screenshot and runs OCR (pytesseract) on it both to pull overlay text baked into the
  image (captions are often not present as separate metadata for stories) and to detect
  "we suspect automated behaviour"/login-wall phrases.
- If `stop_on_suspected_block` is enabled (default), a detected block phrase fails the item immediately
  instead of continuing to hit Facebook with a flagged session.
- Screenshot and WACZ capture of the story page are handled by the existing `screenshot_enricher` and
  `wacz_extractor_enricher` enrichers running later in the pipeline (as for any other URL) - this
  extractor does not duplicate that.

### Notes
- Every card in the bucket (all of an owner's current stories, or a whole highlight) is archived. If the URL
  is `/stories/<bucket id>/<card id>/` (as share links usually are), that card is put first and flagged with
  `requested_card: true`.
- Media is read from the story bucket in the page's embedded JSON (full-size image per photo card,
  `playable_url` per video card), with each card's timestamp/type/id stored on the media.
- Loading and clicking a story registers a view - the owner can see the logged-in account viewed it.
""",
}
