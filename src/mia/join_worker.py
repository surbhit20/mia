from pathlib import Path

from playwright.sync_api import sync_playwright


class JoinWorker:
    def __init__(self, profile_dir: Path = Path("~/.mia/chrome-profile").expanduser()):
        self._profile_dir = profile_dir
        self._playwright = None
        self._context = None
        self._page = None

    def join(self, meet_url: str) -> None:
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self._profile_dir),
            headless=False,
            args=["--use-fake-ui-for-media-stream"],
        )
        self._page = self._context.new_page()
        self._page.goto(meet_url)
        join_button = self._page.get_by_role("button", name="Ask to join").or_(
            self._page.get_by_role("button", name="Join now")
        )
        join_button.click(timeout=30_000)

    def leave(self) -> None:
        if self._page is not None:
            self._page.get_by_role("button", name="Leave call").click(timeout=10_000)
        if self._context is not None:
            self._context.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._page = self._context = self._playwright = None
