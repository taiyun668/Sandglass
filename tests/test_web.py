import hashlib
import unittest
import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from sandglass.resources import WEB_DIR


ROOT = Path(__file__).resolve().parents[1]
INDEX = WEB_DIR / "index.html"
I18N = WEB_DIR / "i18n.js"
DESKTOP = ROOT / "sandglass" / "desktop.py"


class _ScriptSourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() != "script":
            return
        for name, value in attrs:
            if name.casefold() == "src" and value is not None:
                self.sources.append(value)


def _i18n_script_sources(html: str) -> list[str]:
    parser = _ScriptSourceParser()
    parser.feed(html)
    return [
        source for source in parser.sources
        if Path(urlsplit(source).path).name.casefold() == "i18n.js"
    ]


class WebLocalizationTests(unittest.TestCase):
    def test_language_menu_and_catalog_are_shipped(self):
        html = INDEX.read_text(encoding="utf-8")
        catalog = I18N.read_text(encoding="utf-8")

        canonical_catalog = I18N.read_text(encoding="utf-8").encode("utf-8")
        current_cache_version = hashlib.sha256(canonical_catalog).hexdigest()[:12]
        self.assertEqual(
            _i18n_script_sources(html),
            [f"i18n.js?v={current_cache_version}"],
            "the shipped i18n URL must identify the canonical LF catalog content",
        )
        self.assertIn('id="app-menu"', html)
        self.assertIn('data-act="language"', html)
        self.assertIn('sandglass.ui.language', html)
        for locale in (
            "zh-CN", "zh-TW", "en-US", "es-ES", "fr-FR",
            "de-DE", "pt-BR", "ru-RU", "ja-JP", "ko-KR",
        ):
            self.assertIn(f'"{locale}"', catalog)

        self.assertIn('label: "繁體中文"', catalog)
        self.assertIn('lang.startsWith("zh") && /(?:^|[-_])(tw|hk|mo|hant)', catalog)
        self.assertIn('class="app-menu-options"', html)
        self.assertIn('data-act="language-toggle"', html)
        self.assertIn('languageOptions', html)
        self.assertIn('languageOptions: false', html)
        self.assertIn("max-height: 252px;", html)
        self.assertIn("overflow-y: auto;", html)
        self.assertIn(".app-menu-options::-webkit-scrollbar", html)
        self.assertIn("scrollbar-width: none;", html)
        self.assertIn("function revealSelectedLanguage()", html)
        self.assertIn("revealSelectedLanguage();", html)

    def test_i18n_script_parser_ignores_comments_and_keeps_live_duplicates(self):
        html = (
            '<!-- <script src="i18n.js?v=expected"></script> -->'
            "<script defer src='i18n.js?v=stale'></script>"
            "<script src='./i18n.js?v=current' async></script>"
        )
        self.assertEqual(
            _i18n_script_sources(html),
            ["i18n.js?v=stale", "./i18n.js?v=current"],
        )

    def test_update_offer_uses_accessible_badge_and_modal_flow(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertNotIn('id="update-banner"', html)
        self.assertIn('id="update-dialog-root"', html)
        self.assertIn('class="update-badge-wrap"', script)
        self.assertIn('aria-describedby="update-tooltip"', script)
        self.assertIn('aria-haspopup="dialog"', script)
        self.assertIn('aria-expanded=', script)
        self.assertIn('role="tooltip"', script)
        self.assertIn('function updateDialogHtml()', script)
        self.assertIn('role="dialog" aria-modal="true"', script)
        self.assertIn('aria-labelledby="${titleId}" aria-describedby="${bodyId}"', script)
        self.assertIn('data-act="update-cancel"', script)
        self.assertIn('data-act="apply-update"', script)
        self.assertIn('aria-busy="true"', script)
        self.assertIn('class="update-dialog-close"', script)
        self.assertIn('if (state.updateApplying) return;', script)
        self.assertIn('function dialogFocusable()', script)
        self.assertIn('child.inert = updateDialogOpen;', script)
        self.assertIn('child.setAttribute("aria-hidden", "true")', script)
        self.assertIn('if (ev.key === "Tab")', script)
        self.assertIn('if (ev.key === "Escape")', script)
        self.assertIn('function announcementNotesHtml(notes)', script)
        self.assertIn('esc(item)', script)
        self.assertIn('fetch("/api/update/announcement")', script)
        self.assertIn('fetch("/api/update/announcement/dismiss"', script)

    def test_update_apply_posts_only_the_requested_version(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn('body: JSON.stringify({ version: state.update.version })', script)
        self.assertNotIn('body: JSON.stringify({ offer: state.update })', script)

    def test_update_offer_requires_explicit_apply_capability(self):
        """Exercise the real response normalizer so a standalone offer cannot
        leave an unusable install badge behind. Keep this behavioral: deleting
        the capability check must make one of these assertions fail.
        """
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        normalizer = script[script.index("    function normalizeUpdateOffer("):script.index("    async function loadUpdate(")]
        node_program = (
            normalizer + "\n" +
            "process.stdout.write(JSON.stringify([" +
            "normalizeUpdateOffer({version: '2.0.0', ready: true, apply_supported: true})," +
            "normalizeUpdateOffer({version: '2.0.0', ready: true, apply_supported: false})," +
            "normalizeUpdateOffer({version: '2.0.0', ready: true})," +
            "normalizeUpdateOffer({ready: true, apply_supported: true})," +
            "normalizeUpdateOffer({version: '2.0.0', ready: false, apply_supported: true})" +
            "]));"
        )
        completed = subprocess.run(
            [node, "-e", node_program], capture_output=True, text=True,
            encoding="utf-8", check=True,
        )
        offers = json.loads(completed.stdout)
        self.assertEqual(offers[0]["version"], "2.0.0")
        self.assertIsNone(offers[1])
        self.assertIsNone(offers[2])
        self.assertIsNone(offers[3])
        self.assertIsNone(offers[4])

    def test_update_badge_click_and_busy_close_guard_are_real_state_machine_behaviors(self):
        """Run the page's actual badge handler and close guard in Node.

        This is intentionally not a source-text assertion: removing the
        openUpdateDialog() call from the click handler must fail because a
        badge click no longer opens the dialog.
        """
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        html = INDEX.read_text(encoding="utf-8")

        def run_page(page, *, expect_success):
            script = page.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
            open_fn = script[script.index("    function openUpdateDialog("):
                                script.index("    async function applyUpdate(")]
            close_fn = script[script.index("    function closeUpdateDialog("):
                                 script.index("    async function dismissAnnouncement(")]
            click_start = script.index(
                '    document.querySelector(".popover").addEventListener("click", (ev) => {'
            )
            click_end = script.index(
                '    document.addEventListener("click", dismissFloatingMenus);', click_start
            )
            click_handler = script[click_start:click_end]
            node_program = r"""
const state = {
  announcementOpen: false,
  updateDialogOpen: false,
  updateApplying: false,
  updateError: "",
  update: {version: "2.0.0"},
  appMenu: true,
  languageOptions: true,
};
let badgeHandler;
function render() {}
function focusUpdateDialog() {}
function dismissAnnouncement() {}
function requestAnimationFrame() {}
const popover = { addEventListener(kind, callback) { if (kind === "click") badgeHandler = callback; } };
const document = { querySelector(selector) { return selector === ".popover" ? popover : null; } };
""" + open_fn + close_fn + click_handler + r"""
const badge = {
  getAttribute(name) { return name === "data-act" ? "update-badge" : null; },
  closest(selector) { return selector === "[data-act]" ? this : null; },
};
badgeHandler({target: badge});
if (!state.updateDialogOpen) throw new Error("badge click did not open update dialog");
if (state.appMenu || state.languageOptions) throw new Error("badge click did not close menus");
state.updateApplying = true;
closeUpdateDialog();
if (!state.updateDialogOpen) throw new Error("busy update dialog was closed");
"""
            completed = subprocess.run(
                [node, "-e", node_program], capture_output=True, text=True,
                encoding="utf-8",
            )
            if expect_success:
                self.assertEqual(completed.returncode, 0, completed.stderr)
            return completed

        run_page(html, expect_success=True)
        mutated = html.replace("        openUpdateDialog();", "        /* badge opener removed */", 1)
        failed = run_page(mutated, expect_success=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("badge click did not open update dialog", failed.stderr)

    def test_update_failure_responses_preserve_only_the_states_they_can_prove(self):
        """Run non-2xx update and announcement responses through the real JS."""
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        normalizer = script[script.index("    function normalizeUpdateOffer("):
                              script.index("    async function loadUpdate(")]
        load_fn = script[script.index("    async function loadUpdate("):
                           script.index("    async function loadAnnouncement(")]
        dismiss_fn = script[script.index("    async function dismissAnnouncement("):
                             script.index("    function openUpdateDialog(")]
        node_program = r"""
const state = {
  update: {version: "old"},
  announcement: {version: "old"},
  announcementOpen: true,
  announcementDismissing: false,
};
let renders = 0;
function render() { renders += 1; }
function requestAnimationFrame() {}
let fetch = async (url) => url.includes("announcement/dismiss")
  ? ({ok: true, json: async () => ({ok: false, dismissed: false})})
  : ({ok: false});
""" + normalizer + load_fn + dismiss_fn + r"""
(async () => {
  await loadUpdate();
  if (state.update !== null) throw new Error("failed update check kept stale offer");
  await dismissAnnouncement();
  if (!state.announcementOpen) throw new Error("failed announcement dismissal closed dialog");
  if (state.announcementDismissing) throw new Error("failed dismissal stayed busy");
})().catch((error) => { console.error(error.message); process.exit(1); });
"""
        completed = subprocess.run(
            [node, "-e", node_program], capture_output=True, text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_refreshing_an_old_ready_offer_keeps_status_polling_for_the_new_one(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        script = INDEX.read_text(encoding="utf-8").rsplit(
            "<script>", 1
        )[-1].split("</script>", 1)[0]
        normalizer = script[
            script.index("    function normalizeUpdateOffer("):
            script.index("    async function loadUpdate(")
        ]
        load_fn = script[
            script.index("    async function loadUpdate("):
            script.index("    async function loadAnnouncement(")
        ]
        program = r"""
const state={update:null,updatePreparing:false};let renders=0;
function render(){renders++;}
const responses=[
  {version:"9.9.8",ready:true,preparing:true,apply_supported:true},
  {version:"9.9.9",ready:true,preparing:false,apply_supported:true},
];
let fetch=async()=>({ok:true,json:async()=>responses.shift()});
""" + normalizer + load_fn + r"""
(async()=>{
  await loadUpdate();
  if(!state.update||state.update.version!=="9.9.8"||!state.updatePreparing)
    throw new Error("old ready offer stopped preparation polling");
  await loadUpdateStatus();
  if(!state.update||state.update.version!=="9.9.9"||state.updatePreparing)
    throw new Error("new prepared identity did not replace old ready offer");
})().catch((error)=>{console.error(error.message);process.exit(1);});
"""
        completed = subprocess.run(
            [node, "-e", program], capture_output=True, text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_stale_or_unready_click_withdraws_badge_and_polls_local_status(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        script = INDEX.read_text(encoding="utf-8").rsplit(
            "<script>", 1
        )[-1].split("</script>", 1)[0]
        apply_fn = script[
            script.index("    async function applyUpdate() {"):
            script.index("    function runtimeWarningHtml() {")
        ]
        program = r"""
const state={updateApplying:false,update:{version:"9.9.9"},
  updateDialogOpen:true,announcementOpen:false,updateError:"",updatePreparing:false};
let nextError="";function render(){}
let fetch=async()=>({ok:true,json:async()=>({ok:false,error:nextError})});
""" + apply_fn + r"""
(async()=>{
  for(const error of ["update_not_ready","stale_update","no_update"]){
    state.updateApplying=false;state.update={version:"9.9.9"};
    state.updateDialogOpen=true;state.updatePreparing=false;nextError=error;
    await applyUpdate();
    if(state.update!==null||!state.updatePreparing||state.updateDialogOpen||state.updateApplying)
      throw new Error("stale click state survived: "+error);
  }
})().catch((error)=>{console.error(error.message);process.exit(1);});
"""
        completed = subprocess.run(
            [node, "-e", program], capture_output=True, text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_announcement_notes_are_escaped_by_the_real_browser_function(self):
        """Run the page's pure notes renderer in Node without adding a DOM dependency."""
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        esc = script[script.index("    function esc("):script.index("    function pad2(")]
        notes_fn = script[script.index("    function announcementNotesHtml("):script.index("    function updateDialogHtml(")]
        notes = '<img src=x onerror="alert(1)">\n\n- <script>alert(2)</script>\n\n# 修复说明'
        node_program = (
            esc + "\n" + notes_fn + "\n" +
            "process.stdout.write(JSON.stringify(announcementNotesHtml(" +
            json.dumps(notes, ensure_ascii=False) + ")));"
        )
        completed = subprocess.run(
            [node, "-e", node_program], capture_output=True, text=True,
            encoding="utf-8", check=True
        )
        rendered = json.loads(completed.stdout)
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", rendered)
        self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("<h3>修复说明</h3>", rendered)

    def test_update_checks_at_start_and_every_six_hours_without_auto_apply(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn('const UPDATE_POLL_MS = 6 * 60 * 60 * 1000;', script)
        self.assertIn('updatePoll = setInterval(() => {', script)
        self.assertIn('if (!state.updateApplying && !state.updateDialogOpen && !state.announcementOpen) loadUpdate(true);', script)
        self.assertIn('force ? "/api/update?force=1" : "/api/update"', script)
        self.assertIn('state.update = normalizeUpdateOffer(offer);', script)
        self.assertIn('offer.apply_supported === true', script)
        self.assertIn('offer.ready === true', script)
        self.assertIn('fetch("/api/update/status")', script)
        self.assertIn('state.updatePreparing = offer && offer.preparing === true;', script)
        self.assertIn('loadUpdate();\n    loadAnnouncement();\n    startUpdatePoll();', script)
        self.assertEqual(script.count('fetch("/api/update/apply"'), 1)

    def test_update_flow_strings_cover_all_locales(self):
        catalog = I18N.read_text(encoding="utf-8")
        for key in (
            "updateTooltip", "updateConfirmTitle", "updateConfirmDescription",
            "updateCopy", "updateCancel", "updateInstallRestart", "announcementTitle",
            "announcementDismiss", "announcementClose",
        ):
            self.assertEqual(catalog.count(key + ":"), 10, key)
        self.assertIn("updateFlowTranslations", catalog)
        for locale in (
            "zh-CN", "zh-TW", "en-US", "es-ES", "fr-FR",
            "de-DE", "pt-BR", "ru-RU", "ja-JP", "ko-KR",
        ):
            self.assertIn(f'"{locale}": {{', catalog)

    def test_language_menu_supports_keyboard_navigation_and_focus_return(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn('aria-haspopup="menu"', html)
        self.assertIn('aria-controls="app-menu-panel"', html)
        self.assertIn('id="app-menu-panel" role="menu"', html)
        self.assertIn('data-act="help-open" role="menuitem">${helpIcon}', html)
        self.assertIn('const helpIcon = `<svg viewBox="0 0 16 16"', html)
        self.assertIn('<circle cx="8" cy="8" r="6"/>', html)
        self.assertNotIn('data-act="help-open" role="menuitem"><span class="logo sandglass"', html)
        self.assertIn('class="app-menu-options" role="group"', html)
        self.assertIn('aria-haspopup="menu" aria-expanded="${state.languageOptions ? "true" : "false"}"', html)
        self.assertIn('kind === "language-toggle"', script)
        self.assertIn('state.languageOptions = false;', script)
        self.assertIn('ev.__sandglassAppMenuAction = true;', script)
        self.assertIn('!ev.__sandglassAppMenuAction', script)
        self.assertIn('const modeIcon = `<svg class="menu-icon"', script)
        self.assertNotIn('↗', html)
        self.assertIn('["ArrowDown", "ArrowUp", "Home", "End"]', script)
        self.assertIn("function focusFirstAppMenuItem()", script)
        self.assertIn("function closeAppMenuAndRestoreFocus()", script)
        self.assertIn("requestAnimationFrame(focusAppMenuButton)", script)

    def test_offline_help_explains_coverage_without_promoting_user_sources(self):
        html = INDEX.read_text(encoding="utf-8")
        catalog = I18N.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        help_script = script.split("function helpHtml()", 1)[1].split(
            "function adapterSkillPrompt()", 1
        )[0]

        self.assertIn("function helpHtml()", html)
        self.assertIn('menuRow("help-back"', html)
        self.assertNotIn('state.userSources', html)
        self.assertIn('state.help = true;', html)
        self.assertIn("helpAdvanced: false", html)
        self.assertIn('data-act="help-advanced"', html)
        self.assertIn("${state.helpAdvanced ?", html)
        self.assertIn('<div class="help-sections">${sections}</div>${advanced}', html)
        self.assertNotIn('class="help-section help-advanced"', html)
        self.assertIn(".help-view:focus { outline: none; }", html)
        self.assertIn(".help-view .head h1 .name", html)
        self.assertIn(".help-view .head .logo", html)
        self.assertIn("backdrop-filter: blur(22px) saturate(1.24);", html)
        self.assertIn(".help-view::before", html)
        self.assertIn("@keyframes help-aurora", html)
        self.assertIn(".help-section + .help-section::before", html)
        self.assertNotIn(".help-section::after", html)
        self.assertIn(".help-advanced.open", html)
        self.assertIn("outline-offset: -1px;", html)
        self.assertNotIn('helpSource: ""', html)
        self.assertNotIn('data-act="source-toggle"', html)
        self.assertNotIn('class="custom-source-summary"', html)
        self.assertNotIn('class="custom-source-details"', html)
        self.assertNotIn('class="help-source-count"', help_script)
        self.assertNotIn('class="source-account"', help_script)
        self.assertNotIn('data-act="source-totals"', help_script)
        self.assertNotIn('data-act="source-full-window"', help_script)
        self.assertNotIn('data-act="source-display"', help_script)
        self.assertNotIn('data-act="source-accounts"', help_script)
        self.assertNotIn('custom-source-id', help_script)
        self.assertNotIn('custom-source-shadow', help_script)
        self.assertIn('class="help-advanced${state.helpAdvanced ? " open" : ""}"', html)
        self.assertIn(".help-view::before { animation: none; }", html)
        self.assertIn("html::-webkit-scrollbar, body::-webkit-scrollbar { display: none; }", html)
        self.assertIn('["helpStayLocalTitle", "helpStayLocalCopy"]', html)
        self.assertIn('["helpReadOnlyTitle", "helpReadOnlyCopy"]', html)
        self.assertIn('["helpCoverageTitle", "helpCoverageCopy"]', html)
        self.assertIn('["helpBillingTitle", "helpBillingCopy"]', html)
        self.assertNotIn("helpRecordsTitle", html)
        self.assertNotIn("helpSkillUseTitle", html)
        self.assertNotIn("helpSkillMergeTitle", html)
        for key in (
            "helpStayLocalCopy",
            "helpReadOnlyCopy",
            "helpCoverageCopy",
            "helpBillingCopy",
            "helpAdvancedCopy",
            "helpAdvancedNext",
            "helpSkillAgentPrompt",
            "helpSkillCopy",
            "customSourceRecords",
            "customSourceMirror",
        ):
            self.assertIn(f"{key}:", catalog)
        self.assertIn("认识 {recognized} 个字段 · 未知 {unknown} 个字段", catalog)
        self.assertNotIn(
            "https://github.com/taiyun668/Sandglass/tree/main/skills/sandglass-adapter",
            html,
        )
        self.assertNotIn("help-skill-path", html)
        self.assertIn('data-act="adapter-skill-copy"', html)
        self.assertIn("function adapterSkillPrompt()", html)
        self.assertIn('return t("helpSkillAgentPrompt");', html)
        self.assertIn(
            "从 https://github.com/taiyun668/Sandglass 拉取 skills/sandglass-adapter/",
            catalog,
        )
        self.assertIn('helpAdvancedCopy: "把指令交给本机编码 agent"', catalog)
        self.assertIn('helpAdvancedNext: "粘贴到 Claude Code、Codex、Cursor 里运行"', catalog)
        self.assertNotIn("自动补齐账本", catalog)
        self.assertNotIn("不会自动下载或运行适配器", catalog)
        self.assertNotIn("Read docs/custom-source-contract.md before writing code", html)
        self.assertIn('helpTitle: "帮助"', catalog)
        self.assertIn('helpAdvancedTitle: "复杂场景"', catalog)
        self.assertNotIn('helpAdvancedTitle: "高级"', catalog)
        self.assertIn('class="help-advanced-next"', html)
        self.assertIn(".help-advanced-body p.help-advanced-next", html)
        self.assertIn(".help-advanced-toggle {", html)
        self.assertIn("justify-content: center;", html)
        self.assertIn("font-size: 16px;", html)
        self.assertIn(".help-action {", html)
        self.assertIn("min-width: 152px;", html)
        self.assertIn("font-size: 14px;", html)
        self.assertIn(".help-action:hover { filter: brightness(1.06); transform: translateY(-1px); }", html)
        self.assertIn(".help-action:active { filter: brightness(.92); transform: translateY(0) scale(.985); }", html)
        self.assertIn(".help-action.is-copied,", html)
        self.assertIn("transition: filter 120ms var(--ease), transform 120ms var(--ease), box-shadow 120ms var(--ease);", html)
        self.assertIn('class="help-action${state.adapterSkillCopied ? " is-copied" : ""}"', html)
        self.assertIn('help-view" role="region"', html)
        help_script = script.split("function helpHtml()", 1)[1].split(
            "function adapterSkillPrompt()", 1
        )[0]
        self.assertNotIn('logo sandglass', help_script)
        self.assertIn('<h1><span class="name">${esc(t("helpTitle"))}</span></h1>', help_script)
        self.assertNotIn('data-source-account=', html)
        self.assertNotIn('value="__declared__"', html)
        self.assertNotIn('fetch("/api/user-sources/configure"', html)
        self.assertNotIn('data-act="source-full-window"', html)

    def test_dynamic_navigation_restores_keyboard_focus_after_render(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn('aria-current="${on ? "page" : "false"}"', html)
        self.assertIn('class="disclose-chevron"', html)
        self.assertIn('.disclose[aria-expanded="true"] .disclose-chevron', html)
        self.assertIn('aria-hidden="true"><path d="m4 6 4 4 4-4"/>', html)
        self.assertIn('class="account-list-dropdown${motionClass}"', html)
        self.assertIn('role="region" aria-label="${esc(t("accountList"))}"', html)
        self.assertIn('aria-controls="${accountListId}"', html)
        self.assertIn('animation: account-list-dropdown-in', html)
        self.assertIn('animation: account-list-dropdown-out', html)
        self.assertIn('function toggleAccountList()', script)
        self.assertIn('clearAccountListMotionTimer();', script)
        self.assertIn('accountListMotion: ""', script)
        self.assertIn('const motion = reduceMotion ? "" : open ? "opening" : "closing";', script)
        self.assertIn('role="region" tabindex="-1"', html)
        self.assertIn("function focusActiveTab()", script)
        self.assertIn("requestAnimationFrame(focusActiveTab)", script)
        self.assertIn("function focusOverviewPicker()", script)
        self.assertIn("function closeOverviewPickerAndRestoreFocus()", script)
        self.assertIn("requestAnimationFrame(focusOverviewPickerOpener)", script)

    def test_long_localized_labels_have_fixed_width_escape_routes(self):
        html = INDEX.read_text(encoding="utf-8")
        catalog = I18N.read_text(encoding="utf-8")

        self.assertIn('class="tab-label"', html)
        self.assertIn('background-image: url("assets/logo-mark.png")', html)
        self.assertNotIn('.tabs button.on .logo.sandglass { filter: none; }', html)
        self.assertIn("text-overflow: ellipsis;", html)
        self.assertIn("flex-wrap: wrap;", html)
        self.assertIn("overflow-wrap: anywhere;", html)
        self.assertIn('overview: "Aperçu"', catalog)
        self.assertIn('addAccountButton: "+ Ajouter"', catalog)
        self.assertIn('rescan: "Nova busca"', catalog)
        self.assertIn('addAccountButton: "+ Adicionar"', catalog)

    def test_dynamic_interface_copy_lives_in_the_catalog(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        for old_literal in (
            "正在扫描本机账号",
            "账号列表",
            "未归属说明",
            "累计消耗",
            "扫描没有完成",
        ):
            self.assertNotIn(old_literal, script)

    def test_unassigned_usage_is_visible_but_not_an_account(self):
        html = INDEX.read_text(encoding="utf-8")
        catalog = I18N.read_text(encoding="utf-8")

        self.assertIn("unassigned_by_provider", html)

    def test_local_usage_without_accounts_still_loads_and_shows_unassigned(self):
        html = INDEX.read_text(encoding="utf-8")
        catalog = I18N.read_text(encoding="utf-8")
        self.assertIn("state.providers = data.providers || {}", html)
        self.assertIn("state.accounts.length || hasLocalUsageEvidence()", html)
        self.assertIn('capability(p, "local_usage").available === true', html)
        self.assertIn("${unassignedHtml(p)}", html)
        self.assertIn('t("unassignedUsage"', html)
        self.assertIn("unassignedHistory", catalog)

    def test_empty_unavailable_and_signed_out_states_are_explicit(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        catalog = I18N.read_text(encoding="utf-8")

        for key in (
            "notScannedTitle",
            "noLocalLoginTitle",
            "providerNoLoginTitle",
            "quotaUnavailableTitle",
            "authExpired",
            "rateLimited",
        ):
            self.assertIn(f't("{key}")', script)
            self.assertIn(f"{key}:", catalog)
        self.assertIn('t("signedOut")', script)
        self.assertIn('function whenText(w, row, acc)', script)
        self.assertIn('if (acc && acc.quota_source === "stale") return "";', script)

    def test_zero_used_timer_not_started_is_codex_only(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        codex_only = (
            'if (acc && acc.provider === "codex" && '
            'Number(w.used_percent) === 0 && localSpent <= 0) return t("timerNotStarted");'
        )
        self.assertIn(codex_only, script)
        self.assertNotIn(
            'if (Number(w.used_percent) === 0 && localSpent <= 0) return t("timerNotStarted");',
            script,
        )

    def test_third_party_codex_account_source_is_not_shipped(self):
        html = INDEX.read_text(encoding="utf-8")
        catalog = I18N.read_text(encoding="utf-8")

        self.assertNotIn("codex-auth", html)
        self.assertNotIn("codex-auth", catalog)
        self.assertNotIn("thirdPartySource", catalog)

    def test_telemetry_evidence_status_is_visible_without_changing_totals(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        catalog = I18N.read_text(encoding="utf-8")

        self.assertIn('fetch("/api/telemetry-status")', script)
        self.assertIn("function telemetryHtml(provider)", script)
        self.assertIn('row.state === "identity_observed"', script)
        self.assertIn('row.state === "usage_only"', script)
        self.assertIn('row.state === "traffic_only"', script)
        self.assertIn('data-act="telemetry-toggle"', script)
        self.assertIn('data-act="telemetry-enable"', script)
        self.assertIn('class="telemetry-enable"', script)
        self.assertIn('const hasProviderControl = Boolean(', script)
        self.assertIn('const providerEnabled = hasProviderControl ? receiver.providers[provider] === true : true;', script)
        self.assertIn('t("telemetryProviderOff", { provider: NAMES[provider] })', script)
        self.assertIn('t(providerEnabled ? "telemetryProviderEnabled" : "telemetryProviderOff"', script)
        self.assertIn('body: JSON.stringify(provider ? { enabled: true, provider } : { enabled: true })', script)
        self.assertIn('data-provider="${esc(provider)}">${esc(t("telemetryDisableProvider"))}', script)
        self.assertIn('state.telemetryEnabling', script)
        self.assertIn('class="telemetry-status-row"', script)
        self.assertIn('t("telemetryWaiting", { provider: NAMES[provider] })', script)
        self.assertIn('data-act="telemetry-copy"', script)
        self.assertIn('data-act="telemetry-check"', script)
        self.assertIn("CLAUDE_CODE_ENABLE_TELEMETRY", script)
        self.assertIn("GROK_EXTERNAL_OTEL", script)
        self.assertIn("otel.log_user_prompt=false", script)
        self.assertIn("OTEL_LOG_RAW_API_BODIES='0'", script)
        self.assertIn("OTEL_LOG_TOOL_DETAILS='0'", script)
        self.assertIn("telemetryIdentityNote", catalog)
        self.assertIn("telemetryTrafficNote", catalog)
        self.assertIn("尚未并入当前总量", catalog)
        self.assertIn("telemetryEnable:", catalog)
        self.assertIn("telemetryEnabling:", catalog)
        self.assertIn("telemetryWaiting:", catalog)
        self.assertIn("telemetryProviderOff:", catalog)
        self.assertIn("telemetryProviderOffNote:", catalog)
        self.assertIn("telemetryProviderEnabled:", catalog)
        self.assertIn("telemetryDisableProvider:", catalog)
        self.assertIn('telemetryReceiverOff: "精确监测尚未开启"', catalog)

    def test_telemetry_copy_command_uses_path_applications_and_current_codex_exporter(self):
        """The copied command must not assume one install layout or an old TOML shape.

        PowerShell binds `codex` to npm's *.ps1 shim, which ExecutionPolicy
        blocks. Codex 0.149 rejects the inline-table exporter form. The
        command looks up Application shims on PATH instead of a machine path.
        """
        script = INDEX.read_text(encoding="utf-8").rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        self.assertIn("Get-Command $_ -CommandType Application", script)
        self.assertIn("codex.cmd", script)
        self.assertIn("claude.cmd", script)
        self.assertIn("grok.cmd", script)
        self.assertIn("& $cli.Source", script)
        self.assertIn("otel.exporter=otlp-http", script)
        self.assertIn('otel.exporter."otlp-http".endpoint=', script)
        self.assertNotIn("otel.exporter={ otlp-http = {", script)

    def test_freshness_stamp_counts_down_to_backend_quota_refresh(self):
        """The stamp is the vendor cache TTL, not the panel's 20s reread.

        Claude is fetched every 180s and Codex/Grok every 90s. Counting down
        to the frontend poll made every card say 'refresh in 20s' regardless
        of which interface was due. Paint only the stamp nodes so a
        one-second tick cannot steal focus.
        """
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        catalog = I18N.read_text(encoding="utf-8")
        stamp = script.split("function stampDueAt", 1)[1].split("function stampErr", 1)[0]
        self.assertIn("acc.quota_refresh_at", stamp)
        self.assertIn('t("refreshIn"', stamp)
        self.assertLess(stamp.index("const due = stampDueAt(acc)"), stamp.index("state.stale && state.lastOkAt"))
        self.assertIn("function paintStamps()", script)
        self.assertIn("maybePollDueQuota()", script)
        self.assertIn("duePollArmed", script)
        self.assertIn("data-stamp=", script)
        self.assertIn("el.textContent !== next", script)
        self.assertIn("const POLL_MS = 20000", script)
        self.assertNotIn("pollDueAt", script)
        self.assertIn("refreshIn:", catalog)

    def test_quota_load_does_not_treat_an_http_error_as_an_empty_roster(self):
        """Native 404/500 bodies are JSON without accounts.

        fetch() does not throw on those statuses. Reading .json() and taking
        data.accounts || [] would look like a successful scan that found
        nobody, and would stamp lastOkAt.
        """
        script = INDEX.read_text(encoding="utf-8").rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        quota = script.split("async function loadQuota", 1)[1].split("function waitFor", 1)[0]
        self.assertIn('fetch("/api/quota"', quota)
        self.assertIn("new AbortController()", quota)
        self.assertIn("setTimeout(() => scanController.abort(), 20000)", quota)
        self.assertIn("{ signal: scanController.signal }", quota)
        self.assertIn("if (!response.ok) throw new Error(`HTTP ${response.status}`)", quota)
        self.assertIn("if (state.loadingQuota && !onboarding) return", quota)
        self.assertIn("if (!data || !Array.isArray(data.accounts)) throw new Error(\"invalid quota payload\")", quota)
        self.assertIn("if (seq !== state.quotaSeq) return", quota)
        self.assertIn("refreshUsageData(quotaMoved)", quota)
        self.assertIn("acc.quota_fetched_at", quota)

    def test_local_usage_load_is_ordered_retried_and_visible_on_failure(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        catalog = I18N.read_text(encoding="utf-8")

        self.assertIn("let localLoadPromise = null", script)
        self.assertIn("const delays = [700, 1800]", script)
        self.assertIn('fetch("/api/local-windows", { signal: controller.signal })', script)
        self.assertIn("if (!response.ok) throw new Error(`HTTP ${response.status}`)", script)
        self.assertIn("state.localError = true", script)
        self.assertNotIn("state.localError = false", script.split("async function loadReport", 1)[1].split("async function loadTelemetry", 1)[0])
        self.assertIn("async function refreshUsageData(force, userInitiated = false)", script)
        self.assertLess(
            script.index("await loadLocalWindows(force, userInitiated)"),
            script.index("await loadReport(force)"),
        )
        self.assertIn('data-act="local-retry"', script)
        self.assertIn('class="local-sync is-error" role="alert"', script)
        for key in (
            "localUsageLoading", "localUsageRetrying", "localUsageUnavailable", "retry"
        ):
            self.assertIn(f"{key}:", catalog)

    def test_detected_reset_never_extrapolates_a_full_window(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn('if (row.reset_anchored) return `${t("usage"', script)
        self.assertIn('t("resetOccurred")', script)
        self.assertIn("if (row.reset_anchored) {", script)
        self.assertIn('parts.push(t("resetExplanation"))', script)
        self.assertIn("row.full_window_inference_allowed === false", script)
        self.assertIn('parts.push(t("userSourceWindowLocked"))', script)
        self.assertNotIn('data-act="source-totals"', script)
        self.assertNotIn('data-act="source-full-window"', script)
        self.assertIn('t("userAdapterVerified", { sources:', script)

    def test_language_menu_uses_its_own_row_and_dismisses_globally(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn("padding-top: 32px;", html)
        self.assertIn("padding: 8px 10px 7px;", html)
        self.assertIn('document.addEventListener("mousedown"', script)
        self.assertIn('document.addEventListener("click", dismissFloatingMenus)', script)
        self.assertIn('[data-act="overview-guide-add"]', script)

    def test_desktop_close_and_minimize_have_distinct_destinations(self):
        desktop = DESKTOP.read_text(encoding="utf-8")

        self.assertIn('id="shell-minimize"', desktop)
        self.assertIn("window.pywebview.api.minimize_panel()", desktop)
        self.assertIn("self._shell.hide_panel(show_orb=False)", desktop)
        self.assertIn("self._shell.hide_panel(show_orb=True)", desktop)

    def test_activity_heatmap_adapts_7_30_and_180_days_with_latest_at_bottom_right(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn("grid-template-columns: repeat(var(--heat-column-count), var(--heat-cell-size))", html)
        self.assertIn("grid-auto-flow: row", html)
        self.assertIn("justify-content: space-between", html)
        self.assertIn("--heat-column-count: 18", html)
        self.assertIn("--heat-column-count: 10", html)
        self.assertIn("--heat-column-count: 7", html)
        self.assertIn("--heat-cell-size: 14px", html)
        self.assertIn("--heat-cell-size: 24px", html)
        self.assertIn("--heat-cell-size: 32px", html)
        self.assertIn("border-radius: 3px", html)
        self.assertIn("days.slice(-180)", script)
        self.assertNotIn("days.slice(-182)", script)
        self.assertIn('rangeKey === "7" ? 7 : rangeKey === "30" ? 10 : 18', script)
        self.assertIn("const column = index % columns", script)
        self.assertIn('class="heat-axis"', script)
        self.assertIn("heatDays[heatDays.length - 1].day", script)
        self.assertNotIn("heatDays[0].day", script)
        self.assertIn('<div class="heat-axis"><span>${esc(last)}</span></div>', script)
        self.assertIn('state.activityRange === "all" ? days : days.slice(-Number(state.activityRange))', script)
        self.assertIn('state.activityRange === "all" ? days.slice(-180) : viewDays', script)
        self.assertIn('data-act="activity-range"', script)
        self.assertNotIn("month-label", html)
        self.assertIn('const title = v ? `${date} · ${t("used")} ${compact(v)} Token` : date;', script)
        self.assertIn('data-tip="${esc(title)}"', script)
        self.assertIn('aria-hidden="true"', script)
        self.assertIn('role="img" aria-label="${esc(t("activeDaysSummary"', script)
        self.assertNotIn('title="${esc(title)}"', script)
        self.assertIn("animation: heat-cell-in", html)
        self.assertIn(".heat-cell:hover", html)
        self.assertIn(".heat-cell::after", html)

    def test_compact_activity_and_account_controls_remain_readable_and_localized(self):
        html = INDEX.read_text(encoding="utf-8")
        catalog = I18N.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", html)
        self.assertNotIn('t("peakDaily")', script)
        self.assertNotIn('t("currentStreak")', script)
        self.assertIn("font-size: 12px;", html)
        self.assertIn("min-height: 24px;", html)
        self.assertIn("width: fit-content;", html)
        self.assertIn("max-width: 100%;", html)
        self.assertIn("scrollbar-width: none;", html)
        self.assertIn(".accs::-webkit-scrollbar { display: none; }", html)
        self.assertNotIn("scrollbar-width: thin;", html)
        self.assertIn('aria-label="${esc(t("activityRangeLabel"))}"', script)
        self.assertIn('t("rangeAll")', script)
        self.assertNotIn('aria-label="Token activity range"', script)
        for key in ("activityRangeLabel", "rangeAll", "range30d", "range7d", "activeDaysSummary"):
            self.assertIn(f"{key}:", catalog)
        self.assertNotIn("total_tokens", catalog)

    def test_focus_colors_and_keyboard_focus_tokens_are_defined(self):
        html = INDEX.read_text(encoding="utf-8")

        self.assertIn("--ink: var(--text);", html)
        self.assertIn(".history-label:focus-visible {", html)
        self.assertIn("outline: 2px solid var(--sel);", html)

    def test_stale_quota_does_not_leave_rate_limit_error_stuck_on_card(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        stale = 'if (acc && acc.quota_source === "stale") return t("notUpdated");'
        error = 'if (acc && acc.quota_error) return localizedQuotaError(acc.quota_error);'
        self.assertIn(stale, script)
        self.assertLess(script.index(stale), script.index(error))
        self.assertIn('acc.quota_source !== "stale"', script)

    def test_expired_non_live_quota_is_unavailable_not_full(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn("function quotaWindowUnavailable(acc, w)", script)
        self.assertIn("function remainingSeconds(w)", script)
        self.assertIn("w.resets_at", script.split("function remainingSeconds", 1)[1].split("function isExpired", 1)[0])
        self.assertIn("remainingSeconds(w) === 0", script)
        self.assertIn('isExpired(w) && (!acc || acc.quota_source !== "live")', script)
        self.assertIn('t(unavailable ? "notUpdated" : "noWindow")', script)
        self.assertIn("function miniSlotHtml(w, p, lab, acc)", script)
        self.assertIn("function miniLab(w, fallback)", script)
        self.assertIn('miniSlotHtml(five, p, miniLab(five, "5h"), a)', script)
        self.assertIn('miniSlotHtml(week, p, miniLab(week, "7d"), a)', script)
        self.assertIn("w.missing || isExpired(w) ? null : localWin(acc, w)", script)
        self.assertIn('if (fromApi.length) {', script)
        self.assertIn("function sameAccount(row, acc)", script)
        self.assertIn("row.provider === acc.provider && row.account_id === acc.account_id", script)
        self.assertIn('if (acc && acc.quota_source === "stale") return null;', script)

    def test_empty_window_shows_the_captured_fetch_error_over_the_generic_reason(self):
        # A 200 with no usable window and a captured provider error (e.g. "HTTP
        # 404") are two different facts. The meter must say the real one when it
        # has it, not fold a live failure into "no window here".
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        meter = script.split("function meterHtml(acc, w) {", 1)[1].split(
            "\n    function ", 1
        )[0]
        self.assertIn(
            'acc && acc.quota_error\n          ? localizedQuotaError(acc.quota_error)\n'
            '          : t(unavailable ? "notUpdated" : "noWindow")',
            meter,
        )
        self.assertIn('<span class="meter-pct">${esc(reason)}</span>', meter)

        mini = script.split("function miniSlotHtml(w, p, lab, acc) {", 1)[1].split(
            "\n    function ", 1
        )[0]
        self.assertIn(
            'acc && acc.quota_error\n          ? localizedQuotaError(acc.quota_error)\n'
            '          : t(unavailable ? "notUpdated" : "noWindow")',
            mini,
        )
        self.assertIn('aria-label="${esc(`${lab} ${reason}`)}"', mini)

    def test_overview_picker_groups_every_account_and_marks_status(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        catalog = I18N.read_text(encoding="utf-8")

        self.assertIn('const groups = ["codex", "claude", "grok"].map', script)
        self.assertIn('class="overview-provider-group ${esc(provider)}"', script)
        self.assertIn('isCurrent ? `<span class="current">', script)
        self.assertIn('isShown ? `<span>${esc(t("shownOnOverview"))}', script)
        self.assertNotIn("state.accounts.filter(a => !shown.has(accountKey(a)))", script)
        self.assertIn('shownOnOverview: "已展示"', catalog)

    def test_account_monitoring_is_reversible_ui_state_and_overview_has_display_modes(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        catalog = I18N.read_text(encoding="utf-8")

        self.assertIn('hiddenMonitorIds: readListStore("sandglass.ui.hiddenMonitorAccounts") || []', script)
        self.assertIn('writeListStore("sandglass.ui.hiddenMonitorAccounts", state.hiddenMonitorIds)', script)
        self.assertIn('data-act="monitor-cancel"', script)
        self.assertIn('class="account-monitor-cancel"', script)
        self.assertIn('const isCurrent = Boolean(current && current.account_id === a.account_id)', script)
        self.assertIn('class="account-monitor-spacer"', script)
        self.assertIn('account-row-shell${isCurrent ? " is-current" : ""}', script)
        self.assertIn('title="${esc(cancelLabel)}">×</button>', script)
        self.assertIn('class="mini ${esc(p)}"', script)
        self.assertIn('class="win is-empty" aria-label=', script)
        self.assertNotIn('class="pct"', script)
        self.assertIn('data-act="monitor-restore"', script)
        self.assertIn('data-act="monitor-add-toggle"', script)
        self.assertIn('overviewMode: readStore("sandglass.ui.overviewMode", "default")', script)
        self.assertIn('class="overview-mode-toggle"', script)
        self.assertIn('class="ov${state.overviewMode === "custom" ? " removable" : ""}"', script)
        self.assertNotIn('.ov .head .row:first-child { padding-right: 26px; }', html)
        self.assertNotIn('.ov.removable .head .row:first-child { padding-right: 26px; }', html)
        self.assertIn('class="overview-card-actions"', script)
        self.assertIn('.overview-card-actions {', html)
        self.assertIn('justify-content: flex-end;', html)
        self.assertNotIn('.ov.removable:hover .overview-remove', html)
        remove_css = html.split('.overview-remove {', 1)[1].split('.menu {', 1)[0]
        self.assertNotIn('opacity: 0;', remove_css)
        self.assertIn('title="${esc(t("removeAccountAria"', script)
        self.assertIn('function localLine(row, w)', script)
        self.assertIn('const local = localLine(row, w);', script)
        self.assertIn('const meters = metersHtml(acc);', script)
        self.assertNotIn('withUserSources(`${t("usage"', script)
        self.assertIn('data-mode="${nextMode}" data-current="${state.overviewMode}"', script)
        self.assertIn('title="${esc(modeTip)}"', script)
        self.assertNotIn("class=\"mode-icon", script)
        self.assertIn('class="mode-label"', script)
        self.assertIn('background: var(--sel);', html)
        self.assertNotIn('background: var(--blue);', html)
        self.assertIn('.account-row-shell.is-current .account-pick', html)
        self.assertIn('font-size: 14px;', html)
        self.assertIn('grid-template-columns: minmax(0, 1fr) auto;', html)
        self.assertIn('justify-content: flex-end;', html)
        self.assertIn('gap: 12px;', html)
        self.assertIn('.account-row-shell.is-current .account-monitor-spacer { width: 0; }', html)
        self.assertIn('.accs .account-pick.on { background: var(--hover); }', html)
        self.assertNotIn('class="overview-display-mode"', script)
        self.assertIn('defaultOverviewAccounts().map(accountKey)', script)
        self.assertNotIn('fetch("/api/account-monitoring"', script)
        for key in (
            "overviewDefaultMode",
            "overviewCustomMode",
            "addMonitoring",
            "cancelMonitoring",
            "restoreMonitoring",
        ):
            self.assertIn(f"{key}:", catalog)
        self.assertIn('modeSkillTitle: "复杂场景"', catalog)
        self.assertNotIn('modeSkillTitle: "多账号 / 多工具 / 补账"', catalog)

    def test_saved_overview_uses_loading_state_instead_of_onboarding_on_boot(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn("function overviewRestoringHtml()", script)
        self.assertIn("selectedOverviewIds().length > 0 && !state.lastOkAt", script)
        self.assertIn("restoring ? overviewRestoringHtml() : overviewOnboardingHtml()", script)

    def test_selected_product_mode_discovers_accounts_on_every_launch(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn("if (state.productMode.selected) {\n        loadQuota();", script)
        self.assertNotIn(
            "state.productMode.selected && selectedOverviewIds().length",
            script,
        )

    def test_product_mode_fetch_failure_is_not_an_unselected_mode(self):
        """A failed GET is not 'the user has not chosen'. Showing the chooser
        would let a transport error become a mode change."""
        script = INDEX.read_text(encoding="utf-8").rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        load = script.split("async function loadProductMode()", 1)[1].split("async function chooseProductMode", 1)[0]
        self.assertIn("state.productModeError = true", load)
        self.assertNotIn(
            'state.productMode = { selected: false, attribution_mode: "", skill_required: false }',
            load,
        )
        self.assertIn('data-act="mode-reload"', script)
        self.assertIn("state.productModeError && !state.productMode.selected", script)

    def test_reduced_motion_never_disables_account_monitoring(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        start_poll = script.split("function startPoll() {", 1)[1].split("}", 1)[0]

        self.assertIn("if (state.productMode.selected) loadQuota();", start_poll)
        self.assertIn("if (state.updatePreparing", start_poll)
        self.assertIn("loadUpdateStatus();", start_poll)
        self.assertIn("const POLL_MS = 20000", script)
        self.assertNotIn("reduceMotion", start_poll)

    def test_existing_poll_reads_prepare_status_without_quota_before_mode(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        script = INDEX.read_text(encoding="utf-8").rsplit(
            "<script>", 1
        )[-1].split("</script>", 1)[0]
        start_poll = script[
            script.index("    function startPoll() {"):
            script.index("    function startUpdatePoll() {")
        ]
        program = (
            "let quota=0,status=0;"
            "const state={productMode:{selected:false},updatePreparing:true,"
            "updateApplying:false,updateDialogOpen:false,announcementOpen:false};"
            "const POLL_MS=20000;let poll=null,stampTick=null;"
            "function clearInterval(){}"
            "function setInterval(fn){if(typeof fn==='function')fn();return 1;}"
            "function loadQuota(){quota++;}"
            "function loadUpdateStatus(){status++;}"
            "function paintStamps(){}"
            + start_poll +
            "startPoll();const before=[quota,status];"
            "state.productMode.selected=true;state.updatePreparing=false;startPoll();"
            "process.stdout.write(JSON.stringify([before,[quota,status]]));"
        )
        completed = subprocess.run(
            [node, "-e", program], capture_output=True, text=True,
            encoding="utf-8", check=True,
        )
        self.assertEqual(json.loads(completed.stdout), [[0, 1], [1, 1]])

    def test_onboarding_requires_an_explicit_attribution_path(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        catalog = I18N.read_text(encoding="utf-8")

        self.assertIn('fetch("/api/product-mode")', script)
        self.assertIn('chooseProductMode("single_official")', script)
        self.assertIn('chooseProductMode("skill_assisted")', script)
        self.assertIn('data-act="mode-single"', script)
        self.assertIn('data-act="mode-skill"', script)
        self.assertIn('data-act="mode-change"', script)
        self.assertIn('data-act="mode-cancel"', script)
        self.assertIn('state.productModeBeforeEdit = { ...state.productMode }', script)
        self.assertIn('state.help = true;\n          state.helpAdvanced = true;\n          renderTabChange();', script)
        self.assertNotIn('state.accounts.length > 1', script)
        for key in (
            "modeTitle",
            "modeSingleTitle",
            "modeSkillTitle",
            "modeChange",
            "modeCancel",
            "unassignedSkill",
        ):
            self.assertIn(f"{key}:", catalog)

    def test_desktop_recommends_autostart_without_enabling_it_implicitly(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        catalog = I18N.read_text(encoding="utf-8")

        self.assertIn('id="autostart-nudge"', html)
        self.assertIn('data-act="autostart-enable"', script)
        self.assertIn('data-act="autostart-later"', script)
        self.assertIn('await api.autostart_status()', script)
        self.assertIn('await api.enable_autostart()', script)
        self.assertIn('autostartTitle: "建议开启开机自启"', catalog)

    def test_tab_switch_has_a_short_content_transition(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]

        self.assertIn("@keyframes tab-enter", html)
        self.assertIn("function renderTabChange()", script)
        self.assertIn("clearTimeout(renderTabChange.timer)", script)
        self.assertIn("renderTabChange();", script)
        self.assertIn("prefers-reduced-motion: reduce", html)

    def test_runtime_component_failure_is_not_presented_as_account_disconnect(self):
        html = INDEX.read_text(encoding="utf-8")
        script = html.rsplit("<script>", 1)[-1].split("</script>", 1)[0]
        catalog = I18N.read_text(encoding="utf-8")

        self.assertIn('fetch("/api/runtime-diagnostics")', script)
        self.assertIn('issue.status === "blocked_by_application_control"', script)
        self.assertIn('issues.find((item) => item && item.status === "blocked_by_application_control")', script)
        self.assertIn("runtimeBlockedTitle", catalog)
        self.assertIn("runtimeComponentTitle", catalog)
        self.assertIn("这不是账号掉线", catalog)


class SuppressedFullWindowReasonTests(unittest.TestCase):
    """full_window_inference_allowed is set false for two unrelated reasons.

    serve.py turns it off when a provider's quota reading went stale -- an HTTP
    429 is enough -- and telemetry.py turns it off when a user adapter is not
    authorised for full-window inference. The tooltip named only the second, so
    a rate-limited Claude account whose only evidence was official_builtin told
    the user their numbers involved an unauthorised adapter. There was no
    adapter.
    """

    LOCALES = ("zh-CN", "zh-TW", "en-US", "es-ES", "fr-FR",
               "de-DE", "pt-BR", "ru-RU", "ja-JP", "ko-KR")

    def title_block(self):
        html = INDEX.read_text(encoding="utf-8")
        return html.split("function localTitle(", 1)[1].split("function meterHtml", 1)[0]

    def test_the_adapter_wording_is_used_only_when_there_is_an_adapter(self):
        block = self.title_block()
        locked = block.index('t("userSourceWindowLocked")')
        guard = block.rindex("userSourceNames(row).length", 0, locked)
        self.assertLess(guard, locked, "适配器文案必须由适配器证据把关")

    def test_a_stale_quota_and_a_rolled_boundary_have_their_own_wording(self):
        block = self.title_block()
        self.assertIn('t("staleQuotaExplanation")', block)
        self.assertIn('t("rolledWindowExplanation")', block)
        self.assertIn('row.boundary_source === "local_token_timeline"', block)

    def test_both_new_strings_exist_in_every_locale(self):
        catalog = I18N.read_text(encoding="utf-8")
        for key in ("staleQuotaExplanation", "rolledWindowExplanation"):
            self.assertEqual(
                catalog.count(key + ":"), len(self.LOCALES),
                f"{key} 未覆盖全部 {len(self.LOCALES)} 种语言",
            )

    def test_the_backend_still_sets_the_flag_for_both_reasons(self):
        """If either cause disappears the branch above is describing nothing."""
        serve_src = (ROOT / "sandglass" / "serve.py").read_text(encoding="utf-8")
        telemetry_src = (ROOT / "sandglass" / "telemetry.py").read_text(encoding="utf-8")
        assigns = [line.strip() for line in serve_src.splitlines()
                   if line.strip().startswith("inference_allowed =")]
        self.assertTrue(assigns, "serve.py 不再决定 inference_allowed")
        # Later lines narrow it further; what must survive is that a stale
        # quota reading is still one of the reasons it starts out false.
        self.assertTrue(any("stale_quota" in line for line in assigns),
                        f"过期配额不再是抑制满窗的成因之一：{assigns}")
        self.assertIn("full_window_inference_allowed", telemetry_src)


if __name__ == "__main__":
    unittest.main()
