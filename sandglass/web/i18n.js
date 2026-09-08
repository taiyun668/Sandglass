(function () {
  "use strict";

  const messages = {
    "zh-CN": {
      metaDescription: "本机 Claude、Codex、Grok 额度与用量。",
      skipQuota: "跳到额度",
      menu: "菜单",
      language: "语言",
      overview: "概览",
      window5h: "5 小时",
      window7d: "7 天",
      window30d: "30 天",
      window7dOpus: "7 天 Opus",
      window7dSonnet: "7 天 Sonnet",
      remaining: "剩余",
      used: "已使用",
      timerNotStarted: "未开始计时",
      resetNow: "立即重置",
      resetAt: "重置于 {time}",
      resetTomorrowAt: "重置于明天 {time}",
      resetDateAt: "重置于 {date} {time}",
      resetUnderMinute: "不到 1 分钟后重置",
      resetAfter: "{duration}后重置",
      durationDay: "{count}天",
      durationHour: "{count}小时",
      durationMinute: "{count}分钟",
      refreshing: "正在刷新…",
      notUpdated: "未更新",
      updatedNow: "刚刚更新",
      updatedMinutes: "{count} 分钟前已更新",
      updatedHours: "{count} 小时前更新",
      updatedAt: "更新于 {time}",
      usage: "消耗 {value}",
      resetOccurred: "已重置过",
      fullWindow: "满窗 ≈ {value}",
      windowUsage: "本窗消耗 {spent} tokens，产出 {output}",
      reasoning: "（其中思考 {value}）",
      billingExplanation: "。消耗是额度实际计量的量，其中绝大部分是缓存重读。",
      resetExplanation: "这个窗口内检测到额度被重置过，只统计重置之后的部分，不做满窗折算。",
      gapExplanation: "这段时间有观测断档，期间可能发生过额度重置，因此不做满窗折算。",
      estimateExplanation: "按已用 {used}% 折算，整个窗口约 {value} tokens。",
      localOnlyExplanation: "只统计这台电脑；额度条是整个账号。",
      noWindow: "无此窗口",
      totalUsage: "累计消耗",
      peakDaily: "峰值日用量",
      totalOutput: "累计产出",
      daysShort: "{count} 天",
      currentStreak: "当前连续",
      longestStreak: "最长连续",
      tokenActivity: "Token 活动",
      yearlyActivity: "近 1 年 · {count} 天有记录",
      localHeatHint: "只统计这台电脑 · 方块越深，当日 Token 用量越多",
      accountList: "账号列表",
      currentLogin: "当前登录",
      unassignedInfo: "未归属说明 ⓘ",
      unassignedUsage: "未归属 {value} Token ⓘ",
      unassignedHistory: "最近 30 天的本机记录中，这部分用量没有足够的第一方身份事件来确认属于哪个已发现账号。它不会计入任何账号额度卡，Sandglass 也不会猜测或补填。",
      grokHistory: "Sandglass 已开始持续监测该账号的官方额度、重置时间和本机用量归属。开始观测前没有可验证记录的历史用量，将标记为「未归属」，不会猜测或补填。",
      allAccountsShown: "所有已发现账号都在概览中",
      selectAccount: "选择要添加的账号",
      addAccountAria: "添加 {provider} {account}",
      shownOnOverview: "已展示",
      shownAccountAria: "{provider} {account} 已在概览中展示",
      scanningTitle: "正在扫描本机账号…",
      scanningCopy: "正在只读检查 Claude、Codex、Grok 的官方本机登录信息，最迟 20 秒结束。",
      scanning: "扫描中",
      scanningEllipsis: "扫描中…",
      scanComplete: "扫描完成",
      foundAccounts: "发现 {count} 个可识别账号",
      chooseOverview: "选择要放到概览里的账号，之后可以随时添加或取消展示。",
      addAccount: "添加账号",
      stillNoAccounts: "仍未发现可识别账号",
      noAccountsFound: "没有发现可识别账号",
      discoveryBoundary: "Sandglass 只能发现已经由官方客户端登录并留在这台电脑上的账号。未登录、只存在云端或尚未留下本机记录的账号无法扫描出来。",
      loginThenScan: "请先在 Claude、Codex 或 Grok 官方客户端完成登录并使用一次，然后回来重新扫描。",
      rescan: "重新扫描",
      scanFailed: "扫描没有完成",
      serviceUnavailable: "本机服务暂时没有响应，请稍后重试。",
      onboardingTitle: "先找到这台电脑上的 AI 账号",
      onboardingCopy: "Sandglass 只读扫描官方客户端已经留在本机的登录信息，不会修改凭据，也不会切换账号。",
      scanAccounts: "扫描本机账号",
      removeAccountAria: "取消展示 {provider} {account}",
      addAccountButton: "＋ 添加账号",
      refresh: "刷新",
      offlineTitle: "看起来离线了",
      offlineCopy: "连上网之后点刷新，会继续用本机额度。",
      quotaUnavailableTitle: "额度暂时读不到",
      quotaUnavailableCopy: "检查网络后点刷新。已有缓存时会继续显示上次结果。",
      notScannedTitle: "还没有扫描本机账号",
      notScannedCopy: "回到概览，先运行一次只读扫描。",
      noLocalLoginTitle: "本机没有登录账号",
      noLocalLoginCopy: "打开 Claude、Codex 或 Grok 登录后再刷新。",
      signedOut: "未登录",
      providerNoLoginTitle: "这台电脑上没有登录信息",
      providerNoLoginCopy: "在对应客户端登录后点刷新。",
      foundNewAccounts: "发现 {count} 个新账号，请选择添加",
      noNewAccounts: "扫描完成，未发现新账号",
      scanRetryNotice: "扫描没有完成，请稍后重试",
      authExpired: "登录凭据已过期，请回到官方客户端重新登录。",
      rateLimited: "请求受到限制，请稍后重试；若登录已失效，请回到官方客户端重新登录。",
      autostartTitle: "建议开启开机自启",
      autostartCopy: "常驻托盘才能完整记录账号切换、额度变化与重置。不开启仍可补扫 Token，但部分历史无法还原。",
      enableAutostart: "开启",
      later: "暂不",
      autostartFailed: "没有开启成功，请稍后重试，或在托盘菜单中勾选「开机启动」。",
      telemetryReady: "精确监测接收器已就绪 ⓘ",
      telemetryReadyNote: "尚未收到 {provider} 官方客户端发送的 OTLP 用量事件。现有本机日志仍按可证明范围展示；Sandglass 不会把当前账号补到过去。",
      telemetryUsageOnly: "已收到用量证据，缺少账号身份 ⓘ",
      telemetryUsageOnlyNote: "已收到 {provider} 官方用量事件，但事件没有携带账号身份，最近接收于 {time}。这部分保持未归属，尚未并入当前总量。",
      telemetryIdentity: "已收到账号证据 {count} 条 ⓘ",
      telemetryIdentityNote: "已保存 {provider} 官方客户端直接携带账号身份的事件，最近接收于 {time}。这些证据尚未并入当前总量，避免与本机日志重复计算。"
    },
    "zh-TW": {
      metaDescription: "本機 Claude、Codex、Grok 額度與用量。", skipQuota: "跳到額度", menu: "選單", language: "語言", overview: "概覽",
      window5h: "5 小時", window7d: "7 天", window30d: "30 天", window7dOpus: "7 天 Opus", window7dSonnet: "7 天 Sonnet",
      remaining: "剩餘", used: "已使用", timerNotStarted: "尚未開始計時", resetNow: "立即重設", resetAt: "重設於 {time}", resetTomorrowAt: "重設於明天 {time}", resetDateAt: "重設於 {date} {time}", resetUnderMinute: "不到 1 分鐘後重設", resetAfter: "{duration}後重設", durationDay: "{count}天", durationHour: "{count}小時", durationMinute: "{count}分鐘",
      refreshing: "正在重新整理…", notUpdated: "尚未更新", updatedNow: "剛剛更新", updatedMinutes: "{count} 分鐘前更新", updatedHours: "{count} 小時前更新", updatedAt: "更新於 {time}",
      usage: "消耗 {value}", resetOccurred: "已偵測到重設", fullWindow: "完整時窗 ≈ {value}", windowUsage: "本時窗消耗 {spent} tokens，產出 {output}", reasoning: "（其中推理 {value}）", billingExplanation: "。消耗是額度實際計量的量，其中絕大部分是快取重讀。", resetExplanation: "此時窗內偵測到額度重設，只統計重設後的部分，不做完整時窗推算。", gapExplanation: "這段時間有觀測中斷，期間可能發生過額度重設，因此不做完整時窗推算。", estimateExplanation: "按已用 {used}% 推算，完整時窗約 {value} tokens。", localOnlyExplanation: "只統計這台電腦；額度條代表整個帳號。", noWindow: "無此時窗",
      totalUsage: "累計消耗", peakDaily: "單日峰值用量", totalOutput: "累計產出", daysShort: "{count} 天", currentStreak: "目前連續", longestStreak: "最長連續", tokenActivity: "Token 活動", yearlyActivity: "近 1 年 · {count} 天有記錄", localHeatHint: "只統計這台電腦 · 方塊越深，當日 Token 用量越多",
      accountList: "帳號列表", currentLogin: "目前登入", unassignedInfo: "未歸屬說明 ⓘ", unassignedUsage: "未歸屬 {value} Token ⓘ", unassignedHistory: "最近 30 天的本機記錄中，這部分用量沒有足夠的第一方身分事件可確認屬於哪個已發現帳號。它不會計入任何帳號額度卡，Sandglass 也不會猜測或補填。", grokHistory: "Sandglass 已開始持續監測此帳號的官方額度、重設時間及本機用量歸屬。開始觀測前沒有可驗證記錄的歷史用量，將標記為「未歸屬」，不會猜測或補填。",
      allAccountsShown: "所有已發現帳號都在概覽中", selectAccount: "選擇要新增的帳號", addAccountAria: "新增 {provider} {account}", shownOnOverview: "已顯示", shownAccountAria: "{provider} {account} 已在概覽中顯示", scanningTitle: "正在掃描本機帳號…", scanningCopy: "正在以唯讀方式檢查 Claude、Codex、Grok 的官方本機登入資訊，最遲 20 秒結束。", scanning: "掃描中", scanningEllipsis: "掃描中…", scanComplete: "掃描完成", foundAccounts: "發現 {count} 個可辨識帳號", chooseOverview: "選擇要放到概覽中的帳號，之後可隨時新增或取消顯示。", addAccount: "新增帳號", stillNoAccounts: "仍未發現可辨識帳號", noAccountsFound: "沒有發現可辨識帳號", discoveryBoundary: "Sandglass 只能發現已由官方用戶端登入並在這台電腦留下記錄的帳號。未登入、只存在雲端或尚未留下本機記錄的帳號無法掃描出來。", loginThenScan: "請先在 Claude、Codex 或 Grok 官方用戶端完成登入並使用一次，再回來重新掃描。", rescan: "重新掃描", scanFailed: "掃描未完成", serviceUnavailable: "本機服務暫時沒有回應，請稍後再試。", onboardingTitle: "先找到這台電腦上的 AI 帳號", onboardingCopy: "Sandglass 以唯讀方式掃描官方用戶端留在本機的登入資訊，不會修改憑證，也不會切換帳號。", scanAccounts: "掃描本機帳號", removeAccountAria: "取消顯示 {provider} {account}", addAccountButton: "＋ 新增帳號", refresh: "重新整理",
      offlineTitle: "看起來已離線", offlineCopy: "連上網路後重新整理，即可繼續使用本機額度資料。", quotaUnavailableTitle: "暫時無法讀取額度", quotaUnavailableCopy: "檢查網路後重新整理。有快取時會繼續顯示上次結果。", notScannedTitle: "尚未掃描本機帳號", notScannedCopy: "回到概覽，先執行一次唯讀掃描。", noLocalLoginTitle: "本機沒有已登入帳號", noLocalLoginCopy: "登入 Claude、Codex 或 Grok 後再重新整理。", signedOut: "未登入", providerNoLoginTitle: "這台電腦沒有登入資訊", providerNoLoginCopy: "在對應的官方用戶端登入後重新整理。", foundNewAccounts: "發現 {count} 個新帳號，請選擇新增", noNewAccounts: "掃描完成，未發現新帳號", scanRetryNotice: "掃描未完成，請稍後再試", authExpired: "登入憑證已過期，請回官方用戶端重新登入。", rateLimited: "請求受到限制，請稍後再試；若登入已失效，請回官方用戶端重新登入。", autostartTitle: "建議啟用開機自動啟動", autostartCopy: "常駐系統匣才能完整記錄帳號切換、額度變化與重設。不啟用仍可補掃 Token，但部分歷史無法還原。", enableAutostart: "啟用", later: "暫不", autostartFailed: "未能啟用自動啟動，請稍後再試，或在系統匣選單中勾選「開機啟動」。",
      telemetryReady: "精確監測接收器已就緒 ⓘ", telemetryReadyNote: "尚未收到 {provider} 官方用戶端送出的 OTLP 用量事件。現有本機日誌仍按可證明範圍顯示；Sandglass 不會把目前帳號補到過去。", telemetryUsageOnly: "已收到用量證據，但缺少帳號身分 ⓘ", telemetryUsageOnlyNote: "已收到 {provider} 官方用量事件，但事件未攜帶帳號身分，最近接收於 {time}。這部分保持未歸屬，尚未併入目前總量。", telemetryIdentity: "已收到 {count} 筆帳號證據 ⓘ", telemetryIdentityNote: "已儲存直接攜帶帳號身分的 {provider} 官方用戶端事件，最近接收於 {time}。為避免與本機日誌重複計算，尚未併入目前總量。"
    },
    "en-US": {
      metaDescription: "Local Claude, Codex, and Grok quota and usage.", skipQuota: "Skip to quota", menu: "Menu", language: "Language", overview: "Overview",
      window5h: "5 hours", window7d: "7 days", window30d: "30 days", window7dOpus: "7 days Opus", window7dSonnet: "7 days Sonnet",
      remaining: "remaining", used: "used", timerNotStarted: "Timer not started", resetNow: "Resets now", resetAt: "Resets at {time}", resetTomorrowAt: "Resets tomorrow at {time}", resetDateAt: "Resets {date} at {time}", resetUnderMinute: "Resets in under 1 minute", resetAfter: "Resets in {duration}", durationDay: "{count}d", durationHour: "{count}h", durationMinute: "{count}m",
      refreshing: "Refreshing…", notUpdated: "Not updated", updatedNow: "Updated just now", updatedMinutes: "Updated {count} min ago", updatedHours: "Updated {count} hr ago", updatedAt: "Updated at {time}",
      usage: "Usage {value}", resetOccurred: "reset detected", fullWindow: "full window ≈ {value}", windowUsage: "This window used {spent} tokens and produced {output}", reasoning: " (including {value} reasoning)", billingExplanation: ". Usage is the amount counted against quota; most of it is cached input rereads.", resetExplanation: " A quota reset was detected in this window, so only usage after the reset is counted and no full-window estimate is shown.", gapExplanation: " Observation coverage has a gap where a quota reset may have occurred, so no full-window estimate is shown.", estimateExplanation: " At {used}% used, the full window is approximately {value} tokens.", localOnlyExplanation: " Usage is from this computer only; quota bars cover the whole account.", noWindow: "Not available",
      totalUsage: "Total usage", peakDaily: "Peak daily usage", totalOutput: "Total output", daysShort: "{count}d", currentStreak: "Current streak", longestStreak: "Longest streak", tokenActivity: "Token activity", yearlyActivity: "Past year · {count} active days", localHeatHint: "This computer only · Darker squares mean more daily Token usage",
      accountList: "Accounts", currentLogin: "Currently signed in", unassignedInfo: "Unassigned history ⓘ", unassignedUsage: "Unassigned {value} tokens ⓘ", unassignedHistory: "In local records from the past 30 days, this usage lacks enough first-party identity events to link it to a discovered account. It is not included in any account quota card, and Sandglass does not guess or backfill it.", grokHistory: "Sandglass now continuously monitors this account's official quota, reset time, and local usage attribution. Earlier usage without verifiable identity evidence is marked Unassigned; Sandglass does not guess or backfill it.",
      allAccountsShown: "All discovered accounts are already shown", selectAccount: "Choose an account to add", addAccountAria: "Add {provider} {account}", scanningTitle: "Scanning local accounts…", scanningCopy: "Read-only checking official local sign-in data for Claude, Codex, and Grok. This takes at most 20 seconds.", scanning: "Scanning", scanningEllipsis: "Scanning…", scanComplete: "Scan complete", foundAccounts: "Found {count} recognizable accounts", chooseOverview: "Choose accounts to show in Overview. You can add or remove them at any time.", addAccount: "Add account", stillNoAccounts: "Still no recognizable accounts", noAccountsFound: "No recognizable accounts found", discoveryBoundary: "Sandglass can only discover accounts signed in through official clients with records on this computer. It cannot infer signed-out, cloud-only, or not-yet-recorded accounts.", loginThenScan: "Sign in and use Claude, Codex, or Grok once in the official client, then scan again.", rescan: "Scan again", scanFailed: "Scan did not finish", serviceUnavailable: "The local service is not responding. Try again shortly.", onboardingTitle: "Find AI accounts on this computer", onboardingCopy: "Sandglass scans sign-in data left by official clients in read-only mode. It never changes credentials or switches accounts.", scanAccounts: "Scan local accounts", removeAccountAria: "Remove {provider} {account} from Overview", addAccountButton: "+ Add account", refresh: "Refresh",
      shownOnOverview: "Shown", shownAccountAria: "{provider} {account} is already shown in Overview",
      offlineTitle: "You appear to be offline", offlineCopy: "Reconnect, then refresh to continue using local quota data.", quotaUnavailableTitle: "Quota is temporarily unavailable", quotaUnavailableCopy: "Check your connection and refresh. Cached data remains available when present.", notScannedTitle: "Local accounts have not been scanned", notScannedCopy: "Return to Overview and run the read-only scan first.", noLocalLoginTitle: "No signed-in local accounts", noLocalLoginCopy: "Sign in to Claude, Codex, or Grok, then refresh.", signedOut: "Signed out", providerNoLoginTitle: "No sign-in data on this computer", providerNoLoginCopy: "Sign in with the official client, then refresh.", foundNewAccounts: "Found {count} new accounts. Choose which to add", noNewAccounts: "Scan complete. No new accounts found", scanRetryNotice: "Scan did not finish. Try again shortly", authExpired: "Your sign-in has expired. Sign in again with the official client.", rateLimited: "Rate limited. Try again later; if the session expired, sign in again with the official client.", autostartTitle: "Start Sandglass at login", autostartCopy: "Keeping Sandglass in the tray preserves account switches, quota changes, and resets. Token logs can be rescanned later, but some history cannot be reconstructed.", enableAutostart: "Enable", later: "Not now", autostartFailed: "Could not enable startup. Try again or check Start at login in the tray menu.",
      telemetryReady: "Precise receiver ready ⓘ", telemetryReadyNote: "No official {provider} OTLP usage event has been received. Existing local logs remain limited to provable totals and unassigned usage; Sandglass never backfills history from the current account.", telemetryUsageOnly: "Usage evidence received without account identity ⓘ", telemetryUsageOnlyNote: "An official {provider} usage event was received without account identity, most recently at {time}. It remains unassigned and is not yet included in current totals.", telemetryIdentity: "Received {count} account evidence events ⓘ", telemetryIdentityNote: "Official {provider} events carrying account identity have been saved, most recently at {time}. They are not yet included in current totals, preventing duplicates with local logs."
    },
    "es-ES": {
      metaDescription: "Cuotas y uso local de Claude, Codex y Grok.", skipQuota: "Ir a las cuotas", menu: "Menú", language: "Idioma", overview: "Resumen",
      window5h: "5 horas", window7d: "7 días", window30d: "30 días", window7dOpus: "7 días Opus", window7dSonnet: "7 días Sonnet",
      remaining: "restante", used: "usado", timerNotStarted: "Temporizador sin iniciar", resetNow: "Se restablece ahora", resetAt: "Se restablece a las {time}", resetTomorrowAt: "Se restablece mañana a las {time}", resetDateAt: "Se restablece el {date} a las {time}", resetUnderMinute: "Se restablece en menos de 1 minuto", resetAfter: "Se restablece en {duration}", durationDay: "{count} d", durationHour: "{count} h", durationMinute: "{count} min",
      refreshing: "Actualizando…", notUpdated: "Sin actualizar", updatedNow: "Actualizado ahora", updatedMinutes: "Actualizado hace {count} min", updatedHours: "Actualizado hace {count} h", updatedAt: "Actualizado a las {time}",
      usage: "Uso {value}", resetOccurred: "restablecimiento detectado", fullWindow: "ventana completa ≈ {value}", windowUsage: "En esta ventana se usaron {spent} tokens y se generaron {output}", reasoning: " (incluye {value} de razonamiento)", billingExplanation: ". El uso es la cantidad descontada de la cuota; la mayor parte corresponde a relecturas de entrada en caché.", resetExplanation: " Se detectó un restablecimiento de cuota en esta ventana, por lo que solo se cuenta el uso posterior y no se estima la ventana completa.", gapExplanation: " Hay una interrupción en la observación durante la que pudo restablecerse la cuota, así que no se estima la ventana completa.", estimateExplanation: " Con un {used}% usado, la ventana completa equivale aproximadamente a {value} tokens.", localOnlyExplanation: " El uso corresponde solo a este equipo; las barras de cuota abarcan toda la cuenta.", noWindow: "No disponible",
      totalUsage: "Uso acumulado", peakDaily: "Máximo diario", totalOutput: "Producción acumulada", daysShort: "{count} d", currentStreak: "Racha actual", longestStreak: "Racha más larga", tokenActivity: "Actividad de tokens", yearlyActivity: "Último año · {count} días activos", localHeatHint: "Solo este equipo · Cuanto más oscuro, mayor uso diario de Token",
      accountList: "Cuentas", currentLogin: "Sesión actual", unassignedInfo: "Historial sin asignar ⓘ", unassignedUsage: "Sin asignar: {value} tokens ⓘ", unassignedHistory: "En los registros locales de los últimos 30 días, este uso no tiene suficientes eventos de identidad de primera parte para vincularlo a una cuenta detectada. No se incluye en ninguna tarjeta de cuota y Sandglass no lo adivina ni lo rellena.", grokHistory: "Sandglass supervisa desde ahora la cuota oficial, la hora de restablecimiento y la atribución de uso local de esta cuenta. El uso anterior sin pruebas de identidad verificables se marca como Sin asignar; Sandglass no lo adivina ni lo rellena.",
      allAccountsShown: "Ya se muestran todas las cuentas detectadas", selectAccount: "Elige una cuenta para añadir", addAccountAria: "Añadir {provider} {account}", shownOnOverview: "Visible", shownAccountAria: "{provider} {account} ya aparece en el resumen", scanningTitle: "Buscando cuentas locales…", scanningCopy: "Comprobación de solo lectura de los inicios de sesión locales oficiales de Claude, Codex y Grok. Tardará como máximo 20 segundos.", scanning: "Buscando", scanningEllipsis: "Buscando…", scanComplete: "Búsqueda completada", foundAccounts: "Se encontraron {count} cuentas reconocibles", chooseOverview: "Elige las cuentas que aparecerán en el resumen. Puedes añadirlas o quitarlas en cualquier momento.", addAccount: "Añadir cuenta", stillNoAccounts: "Aún no hay cuentas reconocibles", noAccountsFound: "No se encontraron cuentas reconocibles", discoveryBoundary: "Sandglass solo puede detectar cuentas iniciadas en clientes oficiales que hayan dejado registros en este equipo. No puede inferir cuentas cerradas, solo en la nube o aún no registradas.", loginThenScan: "Inicia sesión y usa una vez Claude, Codex o Grok en su cliente oficial y vuelve a buscar.", rescan: "Buscar de nuevo", scanFailed: "La búsqueda no terminó", serviceUnavailable: "El servicio local no responde. Vuelve a intentarlo en unos instantes.", onboardingTitle: "Encuentra las cuentas de IA de este equipo", onboardingCopy: "Sandglass examina en modo de solo lectura los datos de inicio de sesión dejados por clientes oficiales. Nunca cambia credenciales ni alterna cuentas.", scanAccounts: "Buscar cuentas locales", removeAccountAria: "Quitar {provider} {account} del resumen", addAccountButton: "+ Añadir cuenta", refresh: "Actualizar",
      offlineTitle: "Parece que no hay conexión", offlineCopy: "Vuelve a conectarte y actualiza para seguir usando los datos locales de cuota.", quotaUnavailableTitle: "La cuota no está disponible temporalmente", quotaUnavailableCopy: "Comprueba la conexión y actualiza. Los datos en caché seguirán disponibles si existen.", notScannedTitle: "Aún no se han buscado cuentas locales", notScannedCopy: "Vuelve al resumen y ejecuta primero la búsqueda de solo lectura.", noLocalLoginTitle: "No hay cuentas locales con sesión iniciada", noLocalLoginCopy: "Inicia sesión en Claude, Codex o Grok y actualiza.", signedOut: "Sesión cerrada", providerNoLoginTitle: "No hay datos de inicio de sesión en este equipo", providerNoLoginCopy: "Inicia sesión con el cliente oficial y actualiza.", foundNewAccounts: "Se encontraron {count} cuentas nuevas. Elige cuáles añadir", noNewAccounts: "Búsqueda completada. No hay cuentas nuevas", scanRetryNotice: "La búsqueda no terminó. Inténtalo de nuevo en unos instantes", authExpired: "Tu sesión ha caducado. Inicia sesión de nuevo con el cliente oficial.", rateLimited: "Se ha limitado la solicitud. Inténtalo más tarde; si la sesión caducó, vuelve a iniciar sesión con el cliente oficial.", autostartTitle: "Iniciar Sandglass al acceder", autostartCopy: "Mantener Sandglass en la bandeja conserva los cambios de cuenta, de cuota y los restablecimientos. Los registros de tokens pueden releerse después, pero parte del historial no puede reconstruirse.", enableAutostart: "Activar", later: "Ahora no", autostartFailed: "No se pudo activar el inicio automático. Inténtalo de nuevo o marca Iniciar al acceder en el menú de la bandeja.",
      telemetryReady: "Receptor preciso listo ⓘ", telemetryReadyNote: "Aún no se ha recibido ningún evento oficial de uso OTLP de {provider}. Los registros locales existentes siguen limitados a totales demostrables y uso sin asignar; Sandglass nunca rellena el historial con la cuenta actual.", telemetryUsageOnly: "Prueba de uso recibida sin identidad de cuenta ⓘ", telemetryUsageOnlyNote: "Se recibió un evento oficial de uso de {provider} sin identidad de cuenta, por última vez a las {time}. Permanece sin asignar y aún no forma parte de los totales actuales.", telemetryIdentity: "Se recibieron {count} eventos de identidad de cuenta ⓘ", telemetryIdentityNote: "Se guardaron eventos oficiales de {provider} con identidad de cuenta, por última vez a las {time}. Aún no forman parte de los totales actuales para evitar duplicados con los registros locales."
    },
    "fr-FR": {
      metaDescription: "Quotas et utilisation locales de Claude, Codex et Grok.", skipQuota: "Aller aux quotas", menu: "Menu", language: "Langue", overview: "Aperçu",
      window5h: "5 heures", window7d: "7 jours", window30d: "30 jours", window7dOpus: "7 jours Opus", window7dSonnet: "7 jours Sonnet",
      remaining: "restant", used: "utilisé", timerNotStarted: "Minuteur non démarré", resetNow: "Réinitialisation immédiate", resetAt: "Réinitialisation à {time}", resetTomorrowAt: "Réinitialisation demain à {time}", resetDateAt: "Réinitialisation le {date} à {time}", resetUnderMinute: "Réinitialisation dans moins d’une minute", resetAfter: "Réinitialisation dans {duration}", durationDay: "{count} j", durationHour: "{count} h", durationMinute: "{count} min",
      refreshing: "Actualisation…", notUpdated: "Non actualisé", updatedNow: "Actualisé à l’instant", updatedMinutes: "Actualisé il y a {count} min", updatedHours: "Actualisé il y a {count} h", updatedAt: "Actualisé à {time}",
      usage: "Utilisation {value}", resetOccurred: "réinitialisation détectée", fullWindow: "fenêtre complète ≈ {value}", windowUsage: "Cette fenêtre a consommé {spent} tokens et produit {output}", reasoning: " (dont {value} de raisonnement)", billingExplanation: ". L’utilisation correspond au volume décompté du quota ; l’essentiel provient de relectures d’entrées en cache.", resetExplanation: " Une réinitialisation du quota a été détectée dans cette fenêtre : seule l’utilisation postérieure est comptée, sans estimation de la fenêtre complète.", gapExplanation: " L’observation comporte une interruption pendant laquelle le quota a pu être réinitialisé ; aucune estimation de la fenêtre complète n’est donc affichée.", estimateExplanation: " Avec {used}% utilisé, la fenêtre complète représente environ {value} tokens.", localOnlyExplanation: " L’utilisation concerne uniquement cet ordinateur ; les barres de quota couvrent tout le compte.", noWindow: "Indisponible",
      totalUsage: "Utilisation cumulée", peakDaily: "Pic quotidien", totalOutput: "Production cumulée", daysShort: "{count} j", currentStreak: "Série actuelle", longestStreak: "Plus longue série", tokenActivity: "Activité des tokens", yearlyActivity: "12 derniers mois · {count} jours actifs", localHeatHint: "Cet ordinateur uniquement · Plus la case est foncée, plus l’utilisation quotidienne de Token est élevée",
      accountList: "Comptes", currentLogin: "Session actuelle", unassignedInfo: "Historique non attribué ⓘ", unassignedUsage: "Non attribué : {value} tokens ⓘ", unassignedHistory: "Dans les relevés locaux des 30 derniers jours, cette utilisation ne dispose pas d’événements d’identité officiels suffisants pour être reliée à un compte détecté. Elle n’apparaît dans aucune carte de quota et Sandglass ne la devine ni ne la reconstitue.", grokHistory: "Sandglass surveille désormais en continu le quota officiel, l’heure de réinitialisation et l’attribution de l’utilisation locale de ce compte. L’utilisation antérieure sans preuve d’identité vérifiable est marquée Non attribuée ; Sandglass ne la devine ni ne la reconstitue.",
      allAccountsShown: "Tous les comptes détectés sont déjà affichés", selectAccount: "Choisissez un compte à ajouter", addAccountAria: "Ajouter {provider} {account}", shownOnOverview: "Affiché", shownAccountAria: "{provider} {account} est déjà affiché dans l’aperçu", scanningTitle: "Analyse des comptes locaux…", scanningCopy: "Vérification en lecture seule des connexions locales officielles de Claude, Codex et Grok. Elle dure au maximum 20 secondes.", scanning: "Analyse", scanningEllipsis: "Analyse…", scanComplete: "Analyse terminée", foundAccounts: "{count} comptes reconnaissables trouvés", chooseOverview: "Choisissez les comptes à afficher dans l’aperçu. Vous pourrez les ajouter ou les retirer à tout moment.", addAccount: "Ajouter un compte", stillNoAccounts: "Toujours aucun compte reconnaissable", noAccountsFound: "Aucun compte reconnaissable trouvé", discoveryBoundary: "Sandglass ne peut détecter que les comptes connectés par les clients officiels et ayant laissé des traces sur cet ordinateur. Il ne peut pas déduire les comptes déconnectés, uniquement en ligne ou pas encore enregistrés.", loginThenScan: "Connectez-vous et utilisez une fois Claude, Codex ou Grok dans son client officiel, puis relancez l’analyse.", rescan: "Réanalyser", scanFailed: "L’analyse n’a pas abouti", serviceUnavailable: "Le service local ne répond pas. Réessayez dans un instant.", onboardingTitle: "Trouver les comptes d’IA de cet ordinateur", onboardingCopy: "Sandglass analyse en lecture seule les données de connexion laissées par les clients officiels. Il ne modifie jamais les identifiants et ne change jamais de compte.", scanAccounts: "Analyser les comptes locaux", removeAccountAria: "Retirer {provider} {account} de l’aperçu", addAccountButton: "+ Ajouter", refresh: "Actualiser",
      offlineTitle: "Vous semblez hors ligne", offlineCopy: "Reconnectez-vous puis actualisez pour continuer à utiliser les données locales de quota.", quotaUnavailableTitle: "Quota temporairement indisponible", quotaUnavailableCopy: "Vérifiez la connexion puis actualisez. Les données en cache restent affichées lorsqu’elles existent.", notScannedTitle: "Les comptes locaux n’ont pas encore été analysés", notScannedCopy: "Revenez à la vue d’ensemble et lancez d’abord l’analyse en lecture seule.", noLocalLoginTitle: "Aucun compte local connecté", noLocalLoginCopy: "Connectez-vous à Claude, Codex ou Grok, puis actualisez.", signedOut: "Déconnecté", providerNoLoginTitle: "Aucune donnée de connexion sur cet ordinateur", providerNoLoginCopy: "Connectez-vous avec le client officiel, puis actualisez.", foundNewAccounts: "{count} nouveaux comptes trouvés. Choisissez ceux à ajouter", noNewAccounts: "Analyse terminée. Aucun nouveau compte", scanRetryNotice: "L’analyse n’a pas abouti. Réessayez dans un instant", authExpired: "Votre connexion a expiré. Reconnectez-vous avec le client officiel.", rateLimited: "Requête limitée. Réessayez plus tard ; si la session a expiré, reconnectez-vous avec le client officiel.", autostartTitle: "Lancer Sandglass à l’ouverture de session", autostartCopy: "Garder Sandglass dans la zone de notification préserve les changements de compte, de quota et les réinitialisations. Les journaux de tokens peuvent être relus plus tard, mais une partie de l’historique ne peut pas être reconstruite.", enableAutostart: "Activer", later: "Plus tard", autostartFailed: "Impossible d’activer le démarrage automatique. Réessayez ou cochez Lancer à l’ouverture de session dans le menu de la zone de notification.",
      telemetryReady: "Récepteur précis prêt ⓘ", telemetryReadyNote: "Aucun événement officiel d’utilisation OTLP de {provider} n’a encore été reçu. Les journaux locaux restent limités aux totaux démontrables et à l’utilisation non attribuée ; Sandglass ne reconstitue jamais le passé avec le compte actuel.", telemetryUsageOnly: "Preuve d’utilisation reçue sans identité de compte ⓘ", telemetryUsageOnlyNote: "Un événement officiel d’utilisation de {provider} sans identité de compte a été reçu pour la dernière fois à {time}. Il reste non attribué et n’est pas encore inclus dans les totaux actuels.", telemetryIdentity: "{count} événements d’identité de compte reçus ⓘ", telemetryIdentityNote: "Des événements officiels de {provider} contenant l’identité du compte ont été enregistrés pour la dernière fois à {time}. Ils ne sont pas encore inclus dans les totaux actuels afin d’éviter les doublons avec les journaux locaux."
    },
    "de-DE": {
      metaDescription: "Lokale Kontingente und Nutzung von Claude, Codex und Grok.", skipQuota: "Zu den Kontingenten", menu: "Menü", language: "Sprache", overview: "Übersicht",
      window5h: "5 Stunden", window7d: "7 Tage", window30d: "30 Tage", window7dOpus: "7 Tage Opus", window7dSonnet: "7 Tage Sonnet",
      remaining: "verbleibend", used: "genutzt", timerNotStarted: "Zeitfenster noch nicht gestartet", resetNow: "Wird jetzt zurückgesetzt", resetAt: "Zurücksetzung um {time}", resetTomorrowAt: "Zurücksetzung morgen um {time}", resetDateAt: "Zurücksetzung am {date} um {time}", resetUnderMinute: "Zurücksetzung in weniger als 1 Minute", resetAfter: "Zurücksetzung in {duration}", durationDay: "{count} T", durationHour: "{count} Std.", durationMinute: "{count} Min.",
      refreshing: "Aktualisieren…", notUpdated: "Nicht aktualisiert", updatedNow: "Gerade aktualisiert", updatedMinutes: "Vor {count} Min. aktualisiert", updatedHours: "Vor {count} Std. aktualisiert", updatedAt: "Um {time} aktualisiert",
      usage: "Nutzung {value}", resetOccurred: "Zurücksetzung erkannt", fullWindow: "volles Fenster ≈ {value}", windowUsage: "In diesem Fenster wurden {spent} Tokens genutzt und {output} erzeugt", reasoning: " (davon {value} Reasoning)", billingExplanation: ". Die Nutzung ist der tatsächlich vom Kontingent abgezogene Umfang; der Großteil entfällt auf erneut gelesene Cache-Eingaben.", resetExplanation: " In diesem Fenster wurde eine Kontingent-Zurücksetzung erkannt. Daher wird nur die Nutzung danach gezählt und das volle Fenster nicht geschätzt.", gapExplanation: " Die Beobachtung weist eine Lücke auf, in der das Kontingent zurückgesetzt worden sein könnte. Daher wird das volle Fenster nicht geschätzt.", estimateExplanation: " Bei {used}% Nutzung umfasst das volle Fenster ungefähr {value} Tokens.", localOnlyExplanation: " Die Nutzung stammt nur von diesem Computer; Kontingentbalken gelten für das gesamte Konto.", noWindow: "Nicht verfügbar",
      totalUsage: "Gesamtnutzung", peakDaily: "Tageshöchstwert", totalOutput: "Gesamtausgabe", daysShort: "{count} T", currentStreak: "Aktuelle Serie", longestStreak: "Längste Serie", tokenActivity: "Token-Aktivität", yearlyActivity: "Letztes Jahr · {count} aktive Tage", localHeatHint: "Nur dieser Computer · Dunklere Felder bedeuten mehr tägliche Token-Nutzung",
      accountList: "Konten", currentLogin: "Aktuell angemeldet", unassignedInfo: "Nicht zugeordneter Verlauf ⓘ", unassignedUsage: "Nicht zugeordnet: {value} Tokens ⓘ", unassignedHistory: "Für diese Nutzung in den lokalen Aufzeichnungen der letzten 30 Tage fehlen ausreichende Erstanbieter-Identitätsereignisse, um sie einem erkannten Konto zuzuordnen. Sie erscheint in keiner Kontingentkarte; Sandglass rät oder ergänzt nichts.", grokHistory: "Sandglass überwacht ab jetzt fortlaufend das offizielle Kontingent, die Zurücksetzungszeit und die lokale Nutzungszuordnung dieses Kontos. Frühere Nutzung ohne überprüfbare Identitätsnachweise wird als Nicht zugeordnet markiert; Sandglass rät oder ergänzt nichts.",
      allAccountsShown: "Alle erkannten Konten werden bereits angezeigt", selectAccount: "Konto zum Hinzufügen auswählen", addAccountAria: "{provider} {account} hinzufügen", shownOnOverview: "Angezeigt", shownAccountAria: "{provider} {account} wird bereits in der Übersicht angezeigt", scanningTitle: "Lokale Konten werden gesucht…", scanningCopy: "Offizielle lokale Anmeldedaten von Claude, Codex und Grok werden schreibgeschützt geprüft. Dies dauert höchstens 20 Sekunden.", scanning: "Suche läuft", scanningEllipsis: "Suche läuft…", scanComplete: "Suche abgeschlossen", foundAccounts: "{count} erkennbare Konten gefunden", chooseOverview: "Konten für die Übersicht auswählen. Sie können jederzeit hinzugefügt oder entfernt werden.", addAccount: "Konto hinzufügen", stillNoAccounts: "Weiterhin keine erkennbaren Konten", noAccountsFound: "Keine erkennbaren Konten gefunden", discoveryBoundary: "Sandglass kann nur Konten erkennen, die in offiziellen Clients angemeldet sind und Spuren auf diesem Computer hinterlassen haben. Abgemeldete, reine Cloud- oder noch nicht lokal erfasste Konten können nicht abgeleitet werden.", loginThenScan: "In einem offiziellen Claude-, Codex- oder Grok-Client anmelden, ihn einmal verwenden und erneut suchen.", rescan: "Erneut suchen", scanFailed: "Suche nicht abgeschlossen", serviceUnavailable: "Der lokale Dienst antwortet nicht. Bitte in Kürze erneut versuchen.", onboardingTitle: "KI-Konten auf diesem Computer finden", onboardingCopy: "Sandglass liest ausschließlich Anmeldedaten, die offizielle Clients lokal hinterlassen haben. Anmeldedaten werden nie verändert und Konten nie gewechselt.", scanAccounts: "Lokale Konten suchen", removeAccountAria: "{provider} {account} aus der Übersicht entfernen", addAccountButton: "+ Konto hinzufügen", refresh: "Aktualisieren",
      offlineTitle: "Sie scheinen offline zu sein", offlineCopy: "Verbindung wiederherstellen und aktualisieren, um lokale Kontingentdaten weiterzuverwenden.", quotaUnavailableTitle: "Kontingent vorübergehend nicht verfügbar", quotaUnavailableCopy: "Verbindung prüfen und aktualisieren. Vorhandene Cache-Daten bleiben verfügbar.", notScannedTitle: "Lokale Konten wurden noch nicht gesucht", notScannedCopy: "Zur Übersicht zurückkehren und zuerst die schreibgeschützte Suche ausführen.", noLocalLoginTitle: "Keine lokal angemeldeten Konten", noLocalLoginCopy: "Bei Claude, Codex oder Grok anmelden und aktualisieren.", signedOut: "Abgemeldet", providerNoLoginTitle: "Keine Anmeldedaten auf diesem Computer", providerNoLoginCopy: "Im offiziellen Client anmelden und aktualisieren.", foundNewAccounts: "{count} neue Konten gefunden. Bitte die hinzuzufügenden auswählen", noNewAccounts: "Suche abgeschlossen. Keine neuen Konten gefunden", scanRetryNotice: "Suche nicht abgeschlossen. Bitte in Kürze erneut versuchen", authExpired: "Ihre Anmeldung ist abgelaufen. Bitte im offiziellen Client erneut anmelden.", rateLimited: "Anfrage begrenzt. Bitte später erneut versuchen; ist die Sitzung abgelaufen, im offiziellen Client erneut anmelden.", autostartTitle: "Sandglass bei der Anmeldung starten", autostartCopy: "Sandglass in der Taskleiste zu belassen, bewahrt Kontowechsel, Kontingentänderungen und Zurücksetzungen. Token-Protokolle können später erneut gelesen werden, manche Historie lässt sich jedoch nicht rekonstruieren.", enableAutostart: "Aktivieren", later: "Später", autostartFailed: "Autostart konnte nicht aktiviert werden. Erneut versuchen oder Im Tray-Menü Bei Anmeldung starten auswählen.",
      telemetryReady: "Präziser Empfänger bereit ⓘ", telemetryReadyNote: "Es wurde noch kein offizielles OTLP-Nutzungsereignis von {provider} empfangen. Vorhandene lokale Protokolle bleiben auf belegbare Summen und nicht zugeordnete Nutzung beschränkt; Sandglass ergänzt die Vergangenheit nie anhand des aktuellen Kontos.", telemetryUsageOnly: "Nutzungsnachweis ohne Kontoidentität empfangen ⓘ", telemetryUsageOnlyNote: "Ein offizielles Nutzungsereignis von {provider} ohne Kontoidentität wurde zuletzt um {time} empfangen. Es bleibt nicht zugeordnet und ist noch nicht in den aktuellen Summen enthalten.", telemetryIdentity: "{count} Kontoidentitätsereignisse empfangen ⓘ", telemetryIdentityNote: "Offizielle Ereignisse von {provider} mit Kontoidentität wurden zuletzt um {time} gespeichert. Sie sind noch nicht in den aktuellen Summen enthalten, um Duplikate mit lokalen Protokollen zu vermeiden."
    },
    "pt-BR": {
      metaDescription: "Cotas e uso local de Claude, Codex e Grok.", skipQuota: "Ir para as cotas", menu: "Menu", language: "Idioma", overview: "Visão geral",
      window5h: "5 horas", window7d: "7 dias", window30d: "30 dias", window7dOpus: "7 dias Opus", window7dSonnet: "7 dias Sonnet",
      remaining: "restante", used: "usado", timerNotStarted: "Contagem ainda não iniciada", resetNow: "Redefine agora", resetAt: "Redefine às {time}", resetTomorrowAt: "Redefine amanhã às {time}", resetDateAt: "Redefine em {date} às {time}", resetUnderMinute: "Redefine em menos de 1 minuto", resetAfter: "Redefine em {duration}", durationDay: "{count} d", durationHour: "{count} h", durationMinute: "{count} min",
      refreshing: "Atualizando…", notUpdated: "Não atualizado", updatedNow: "Atualizado agora", updatedMinutes: "Atualizado há {count} min", updatedHours: "Atualizado há {count} h", updatedAt: "Atualizado às {time}",
      usage: "Uso {value}", resetOccurred: "redefinição detectada", fullWindow: "janela completa ≈ {value}", windowUsage: "Nesta janela, foram usados {spent} tokens e produzidos {output}", reasoning: " (incluindo {value} de raciocínio)", billingExplanation: ". O uso é o volume descontado da cota; a maior parte corresponde a releituras de entradas em cache.", resetExplanation: " Uma redefinição de cota foi detectada nesta janela; por isso, apenas o uso posterior é contado e nenhuma estimativa da janela completa é exibida.", gapExplanation: " Há uma lacuna na observação durante a qual a cota pode ter sido redefinida; por isso, nenhuma estimativa da janela completa é exibida.", estimateExplanation: " Com {used}% usado, a janela completa equivale a aproximadamente {value} tokens.", localOnlyExplanation: " O uso é apenas deste computador; as barras de cota representam a conta inteira.", noWindow: "Indisponível",
      totalUsage: "Uso acumulado", peakDaily: "Pico diário", totalOutput: "Produção acumulada", daysShort: "{count} d", currentStreak: "Sequência atual", longestStreak: "Maior sequência", tokenActivity: "Atividade de tokens", yearlyActivity: "Último ano · {count} dias ativos", localHeatHint: "Apenas este computador · Quanto mais escuro, maior o uso diário de Token",
      accountList: "Contas", currentLogin: "Sessão atual", unassignedInfo: "Histórico não atribuído ⓘ", unassignedUsage: "Não atribuído: {value} tokens ⓘ", unassignedHistory: "Nos registros locais dos últimos 30 dias, este uso não tem eventos de identidade oficiais suficientes para vinculá-lo a uma conta encontrada. Ele não entra no cartão de cota de nenhuma conta, e o Sandglass não adivinha nem preenche esses dados.", grokHistory: "O Sandglass passou a monitorar continuamente a cota oficial, o horário de redefinição e a atribuição do uso local desta conta. O uso anterior sem prova verificável de identidade é marcado como Não atribuído; o Sandglass não adivinha nem preenche esses dados.",
      allAccountsShown: "Todas as contas encontradas já estão visíveis", selectAccount: "Escolha uma conta para adicionar", addAccountAria: "Adicionar {provider} {account}", shownOnOverview: "Visível", shownAccountAria: "{provider} {account} já está na visão geral", scanningTitle: "Procurando contas locais…", scanningCopy: "Verificação somente leitura dos dados oficiais de login local de Claude, Codex e Grok. Leva no máximo 20 segundos.", scanning: "Procurando", scanningEllipsis: "Procurando…", scanComplete: "Busca concluída", foundAccounts: "{count} contas reconhecíveis encontradas", chooseOverview: "Escolha as contas que aparecerão na visão geral. Você pode adicioná-las ou removê-las a qualquer momento.", addAccount: "Adicionar conta", stillNoAccounts: "Ainda não há contas reconhecíveis", noAccountsFound: "Nenhuma conta reconhecível encontrada", discoveryBoundary: "O Sandglass só encontra contas conectadas por clientes oficiais que deixaram registros neste computador. Não é possível inferir contas desconectadas, apenas na nuvem ou ainda sem registros locais.", loginThenScan: "Entre e use Claude, Codex ou Grok uma vez no cliente oficial e procure novamente.", rescan: "Nova busca", scanFailed: "A busca não foi concluída", serviceUnavailable: "O serviço local não está respondendo. Tente novamente em instantes.", onboardingTitle: "Encontre as contas de IA deste computador", onboardingCopy: "O Sandglass examina em modo somente leitura os dados de login deixados por clientes oficiais. Ele nunca altera credenciais nem troca contas.", scanAccounts: "Procurar contas locais", removeAccountAria: "Remover {provider} {account} da visão geral", addAccountButton: "+ Adicionar", refresh: "Atualizar",
      offlineTitle: "Parece que você está offline", offlineCopy: "Reconecte-se e atualize para continuar usando os dados locais de cota.", quotaUnavailableTitle: "Cota temporariamente indisponível", quotaUnavailableCopy: "Verifique a conexão e atualize. Os dados em cache continuam disponíveis quando existem.", notScannedTitle: "As contas locais ainda não foram procuradas", notScannedCopy: "Volte à visão geral e execute primeiro a busca somente leitura.", noLocalLoginTitle: "Nenhuma conta local conectada", noLocalLoginCopy: "Entre no Claude, Codex ou Grok e atualize.", signedOut: "Desconectado", providerNoLoginTitle: "Nenhum dado de login neste computador", providerNoLoginCopy: "Entre pelo cliente oficial e atualize.", foundNewAccounts: "{count} contas novas encontradas. Escolha quais adicionar", noNewAccounts: "Busca concluída. Nenhuma conta nova", scanRetryNotice: "A busca não foi concluída. Tente novamente em instantes", authExpired: "Seu login expirou. Entre novamente pelo cliente oficial.", rateLimited: "Solicitação limitada. Tente mais tarde; se a sessão expirou, entre novamente pelo cliente oficial.", autostartTitle: "Iniciar o Sandglass ao entrar", autostartCopy: "Manter o Sandglass na bandeja preserva trocas de conta, alterações de cota e redefinições. Os registros de tokens podem ser relidos depois, mas parte do histórico não pode ser reconstruída.", enableAutostart: "Ativar", later: "Agora não", autostartFailed: "Não foi possível ativar a inicialização automática. Tente novamente ou marque Iniciar ao entrar no menu da bandeja.",
      telemetryReady: "Receptor preciso pronto ⓘ", telemetryReadyNote: "Ainda não foi recebido nenhum evento oficial de uso OTLP de {provider}. Os registros locais existentes continuam limitados a totais comprováveis e uso não atribuído; o Sandglass nunca preenche o passado com a conta atual.", telemetryUsageOnly: "Prova de uso recebida sem identidade da conta ⓘ", telemetryUsageOnlyNote: "Um evento oficial de uso de {provider} sem identidade da conta foi recebido pela última vez às {time}. Ele permanece não atribuído e ainda não entra nos totais atuais.", telemetryIdentity: "{count} eventos de identidade da conta recebidos ⓘ", telemetryIdentityNote: "Eventos oficiais de {provider} contendo a identidade da conta foram salvos pela última vez às {time}. Eles ainda não entram nos totais atuais para evitar duplicação com os registros locais."
    },
    "ru-RU": {
      metaDescription: "Локальные лимиты и расход Claude, Codex и Grok.", skipQuota: "Перейти к лимитам", menu: "Меню", language: "Язык", overview: "Обзор",
      window5h: "5 часов", window7d: "7 дней", window30d: "30 дней", window7dOpus: "7 дней Opus", window7dSonnet: "7 дней Sonnet",
      remaining: "осталось", used: "использовано", timerNotStarted: "Отсчёт не начался", resetNow: "Сброс сейчас", resetAt: "Сброс в {time}", resetTomorrowAt: "Сброс завтра в {time}", resetDateAt: "Сброс {date} в {time}", resetUnderMinute: "Сброс менее чем через минуту", resetAfter: "Сброс через {duration}", durationDay: "{count} дн.", durationHour: "{count} ч", durationMinute: "{count} мин",
      refreshing: "Обновление…", notUpdated: "Не обновлено", updatedNow: "Только что обновлено", updatedMinutes: "Обновлено {count} мин назад", updatedHours: "Обновлено {count} ч назад", updatedAt: "Обновлено в {time}",
      usage: "Расход {value}", resetOccurred: "обнаружен сброс", fullWindow: "полное окно ≈ {value}", windowUsage: "В этом окне израсходовано {spent} токенов и создано {output}", reasoning: " (включая {value} reasoning)", billingExplanation: ". Расход — это объём, учтённый в лимите; большая его часть приходится на повторное чтение кэшированного ввода.", resetExplanation: " В этом окне обнаружен сброс лимита, поэтому учитывается только расход после сброса, без оценки полного окна.", gapExplanation: " В наблюдении есть пропуск, во время которого мог произойти сброс лимита, поэтому оценка полного окна не показывается.", estimateExplanation: " При использовании {used}% полное окно составляет примерно {value} токенов.", localOnlyExplanation: " Расход учитывает только этот компьютер; полосы лимитов относятся ко всей учётной записи.", noWindow: "Недоступно",
      totalUsage: "Общий расход", peakDaily: "Пиковый расход за день", totalOutput: "Общий вывод", daysShort: "{count} дн.", currentStreak: "Текущая серия", longestStreak: "Самая длинная серия", tokenActivity: "Активность токенов", yearlyActivity: "Последний год · активных дней: {count}", localHeatHint: "Только этот компьютер · Чем темнее клетка, тем больше расход Token за день",
      accountList: "Учётные записи", currentLogin: "Текущий вход", unassignedInfo: "Нераспределённая история ⓘ", unassignedUsage: "Не распределено: {value} токенов ⓘ", unassignedHistory: "В локальных записях за последние 30 дней для этого расхода недостаточно событий идентификации из первичного источника, чтобы связать его с найденной учётной записью. Он не включается ни в одну карточку лимита, а Sandglass ничего не угадывает и не дополняет.", grokHistory: "Теперь Sandglass постоянно отслеживает официальный лимит, время сброса и распределение локального расхода этой учётной записи. Более ранний расход без проверяемых данных идентификации помечается как Нераспределённый; Sandglass ничего не угадывает и не дополняет.",
      allAccountsShown: "Все найденные учётные записи уже показаны", selectAccount: "Выберите учётную запись для добавления", addAccountAria: "Добавить {provider} {account}", shownOnOverview: "Показана", shownAccountAria: "{provider} {account} уже показана в обзоре", scanningTitle: "Поиск локальных учётных записей…", scanningCopy: "Официальные локальные данные входа Claude, Codex и Grok проверяются только для чтения. Это займёт не более 20 секунд.", scanning: "Поиск", scanningEllipsis: "Поиск…", scanComplete: "Поиск завершён", foundAccounts: "Найдено распознаваемых учётных записей: {count}", chooseOverview: "Выберите учётные записи для обзора. Их можно добавить или убрать в любое время.", addAccount: "Добавить учётную запись", stillNoAccounts: "Распознаваемые учётные записи по-прежнему не найдены", noAccountsFound: "Распознаваемые учётные записи не найдены", discoveryBoundary: "Sandglass может найти только учётные записи, в которые выполнен вход через официальные клиенты и которые оставили данные на этом компьютере. Нельзя определить вышедшие, только облачные или ещё не записанные локально учётные записи.", loginThenScan: "Войдите и один раз воспользуйтесь Claude, Codex или Grok в официальном клиенте, затем повторите поиск.", rescan: "Искать снова", scanFailed: "Поиск не завершён", serviceUnavailable: "Локальная служба не отвечает. Повторите попытку чуть позже.", onboardingTitle: "Найдите учётные записи ИИ на этом компьютере", onboardingCopy: "Sandglass только читает данные входа, оставленные официальными клиентами. Он никогда не меняет учётные данные и не переключает учётные записи.", scanAccounts: "Найти локальные учётные записи", removeAccountAria: "Убрать {provider} {account} из обзора", addAccountButton: "+ Добавить учётную запись", refresh: "Обновить",
      offlineTitle: "Похоже, вы не в сети", offlineCopy: "Подключитесь снова и обновите страницу, чтобы продолжить использовать локальные данные лимитов.", quotaUnavailableTitle: "Лимит временно недоступен", quotaUnavailableCopy: "Проверьте подключение и обновите страницу. Кэшированные данные останутся доступны, если они есть.", notScannedTitle: "Локальные учётные записи ещё не найдены", notScannedCopy: "Вернитесь в обзор и сначала запустите поиск только для чтения.", noLocalLoginTitle: "Нет локальных учётных записей с выполненным входом", noLocalLoginCopy: "Войдите в Claude, Codex или Grok и обновите страницу.", signedOut: "Вход не выполнен", providerNoLoginTitle: "На этом компьютере нет данных входа", providerNoLoginCopy: "Войдите через официальный клиент и обновите страницу.", foundNewAccounts: "Найдено новых учётных записей: {count}. Выберите, какие добавить", noNewAccounts: "Поиск завершён. Новых учётных записей нет", scanRetryNotice: "Поиск не завершён. Повторите попытку чуть позже", authExpired: "Срок действия входа истёк. Войдите снова через официальный клиент.", rateLimited: "Частота запросов ограничена. Повторите попытку позже; если сеанс истёк, войдите снова через официальный клиент.", autostartTitle: "Запускать Sandglass при входе", autostartCopy: "Работа Sandglass в области уведомлений сохраняет переключения учётных записей, изменения лимитов и сбросы. Журналы токенов можно перечитать позже, но часть истории восстановить невозможно.", enableAutostart: "Включить", later: "Не сейчас", autostartFailed: "Не удалось включить автозапуск. Повторите попытку или выберите Запускать при входе в меню области уведомлений.",
      telemetryReady: "Точный приёмник готов ⓘ", telemetryReadyNote: "Официальные события использования OTLP от {provider} ещё не поступали. Существующие локальные журналы по-прежнему ограничены доказуемыми итогами и нераспределённым расходом; Sandglass никогда не дополняет прошлое на основе текущей учётной записи.", telemetryUsageOnly: "Получено подтверждение расхода без идентификатора учётной записи ⓘ", telemetryUsageOnlyNote: "Официальное событие использования {provider} без идентификатора учётной записи последний раз получено в {time}. Оно остаётся нераспределённым и пока не включено в текущие итоги.", telemetryIdentity: "Получено событий идентификации учётной записи: {count} ⓘ", telemetryIdentityNote: "Официальные события {provider} с идентификатором учётной записи последний раз сохранены в {time}. Они пока не включены в текущие итоги, чтобы избежать дублирования с локальными журналами."
    },
    "ja-JP": {
      metaDescription: "このPC上の Claude、Codex、Grok の上限と使用量。", skipQuota: "上限情報へ移動", menu: "メニュー", language: "言語", overview: "概要",
      window5h: "5時間", window7d: "7日", window30d: "30日", window7dOpus: "7日 Opus", window7dSonnet: "7日 Sonnet",
      remaining: "残り", used: "使用済み", timerNotStarted: "計測未開始", resetNow: "まもなくリセット", resetAt: "{time} にリセット", resetTomorrowAt: "明日 {time} にリセット", resetDateAt: "{date} {time} にリセット", resetUnderMinute: "1分以内にリセット", resetAfter: "{duration}後にリセット", durationDay: "{count}日", durationHour: "{count}時間", durationMinute: "{count}分",
      refreshing: "更新中…", notUpdated: "未更新", updatedNow: "たった今更新", updatedMinutes: "{count}分前に更新", updatedHours: "{count}時間前に更新", updatedAt: "{time} に更新",
      usage: "使用量 {value}", resetOccurred: "リセット検出済み", fullWindow: "全期間 ≈ {value}", windowUsage: "この期間の使用量は {spent} tokens、出力は {output}", reasoning: "（reasoning {value} を含む）", billingExplanation: "。使用量は上限に計上された量で、その大部分はキャッシュ入力の再読み込みです。", resetExplanation: "この期間中に上限のリセットを検出したため、リセット後だけを集計し、全期間の推定は行いません。", gapExplanation: "観測に欠落があり、その間にリセットされた可能性があるため、全期間の推定は行いません。", estimateExplanation: "使用済み {used}% から、全期間は約 {value} tokens と推定されます。", localOnlyExplanation: "使用量はこのPCのみ、上限バーはアカウント全体です。", noWindow: "対象期間なし",
      totalUsage: "累計使用量", peakDaily: "1日の最大使用量", totalOutput: "累計出力", daysShort: "{count}日", currentStreak: "現在の連続日数", longestStreak: "最長連続日数", tokenActivity: "Token アクティビティ", yearlyActivity: "過去1年 · 記録 {count}日", localHeatHint: "このPCのみ · 色が濃いほど、その日の Token 使用量が多い",
      accountList: "アカウント", currentLogin: "現在のログイン", unassignedInfo: "未割り当て履歴 ⓘ", unassignedUsage: "未割り当て {value} Token ⓘ", unassignedHistory: "過去30日間のこのPCの記録のうち、この使用量を検出済みアカウントに結び付ける十分な公式の本人確認イベントがありません。どのアカウントの上限カードにも含めず、推測や補完も行いません。", grokHistory: "Sandglass はこのアカウントの公式上限、リセット時刻、このPCでの使用量割り当てを継続監視します。観測開始前の本人確認できない履歴は「未割り当て」とし、推測や補完は行いません。",
      allAccountsShown: "検出した全アカウントを表示中", selectAccount: "追加するアカウントを選択", addAccountAria: "{provider} {account} を追加", scanningTitle: "このPCのアカウントをスキャン中…", scanningCopy: "Claude、Codex、Grok の公式ローカルログイン情報を読み取り専用で確認しています。最大20秒かかります。", scanning: "スキャン中", scanningEllipsis: "スキャン中…", scanComplete: "スキャン完了", foundAccounts: "認識可能なアカウントを {count} 件検出", chooseOverview: "概要に表示するアカウントを選択してください。いつでも追加・削除できます。", addAccount: "アカウントを追加", stillNoAccounts: "認識可能なアカウントはまだありません", noAccountsFound: "認識可能なアカウントがありません", discoveryBoundary: "Sandglass が検出できるのは、公式クライアントでログインし、このPCに記録があるアカウントだけです。ログアウト済み、クラウドのみ、未記録のアカウントは推測できません。", loginThenScan: "公式クライアントで Claude、Codex、Grok のいずれかにログインして一度使用し、再度スキャンしてください。", rescan: "再スキャン", scanFailed: "スキャンを完了できませんでした", serviceUnavailable: "ローカルサービスが応答していません。しばらくしてから再試行してください。", onboardingTitle: "このPCの AI アカウントを探す", onboardingCopy: "Sandglass は公式クライアントがこのPCに残したログイン情報を読み取り専用で確認します。認証情報の変更やアカウント切り替えは行いません。", scanAccounts: "このPCをスキャン", removeAccountAria: "概要から {provider} {account} を削除", addAccountButton: "＋ アカウント追加", refresh: "更新",
      shownOnOverview: "表示中", shownAccountAria: "{provider} {account} は概要に表示済みです",
      offlineTitle: "オフラインのようです", offlineCopy: "接続後に更新すると、ローカルの上限データを引き続き利用できます。", quotaUnavailableTitle: "上限情報を一時的に取得できません", quotaUnavailableCopy: "接続を確認して更新してください。キャッシュがあれば前回のデータを表示します。", notScannedTitle: "このPCをまだスキャンしていません", notScannedCopy: "概要に戻り、読み取り専用スキャンを実行してください。", noLocalLoginTitle: "ログイン済みアカウントがありません", noLocalLoginCopy: "Claude、Codex、Grok のいずれかにログインして更新してください。", signedOut: "未ログイン", providerNoLoginTitle: "このPCにログイン情報がありません", providerNoLoginCopy: "公式クライアントでログインして更新してください。", foundNewAccounts: "新しいアカウントを {count} 件検出しました。追加するものを選択してください", noNewAccounts: "スキャン完了。新しいアカウントはありません", scanRetryNotice: "スキャンを完了できませんでした。しばらくして再試行してください", authExpired: "ログインの有効期限が切れました。公式クライアントで再ログインしてください。", rateLimited: "リクエストが制限されています。しばらくしてから再試行し、セッション切れの場合は公式クライアントで再ログインしてください。", autostartTitle: "ログイン時の自動起動を推奨", autostartCopy: "トレイ常駐により、アカウント切替、上限変化、リセットを完全に記録できます。Token は後から再スキャンできますが、一部の履歴は復元できません。", enableAutostart: "有効にする", later: "今はしない", autostartFailed: "自動起動を有効にできませんでした。再試行するか、トレイメニューで設定してください。",
      telemetryReady: "精密監視の受信準備完了 ⓘ", telemetryReadyNote: "{provider} 公式クライアントから OTLP 使用量イベントをまだ受信していません。既存のローカルログは証明可能な範囲と未割り当てだけを表示し、現在のアカウントで過去を補完しません。", telemetryUsageOnly: "アカウント情報なしの使用量証拠を受信 ⓘ", telemetryUsageOnlyNote: "{provider} の公式使用量イベントを受信しましたが、アカウント情報がありません。最終受信は {time} です。未割り当てのまま、現在の合計にはまだ含めません。", telemetryIdentity: "アカウント証拠を {count} 件受信 ⓘ", telemetryIdentityNote: "アカウント情報を直接含む {provider} 公式イベントを保存しました。最終受信は {time} です。ローカルログとの重複を避けるため、現在の合計にはまだ含めません。"
    },
    "ko-KR": {
      metaDescription: "이 PC의 Claude, Codex, Grok 한도와 사용량입니다.", skipQuota: "한도 정보로 이동", menu: "메뉴", language: "언어", overview: "개요",
      window5h: "5시간", window7d: "7일", window30d: "30일", window7dOpus: "7일 Opus", window7dSonnet: "7일 Sonnet",
      remaining: "남음", used: "사용됨", timerNotStarted: "측정 시작 전", resetNow: "지금 초기화", resetAt: "{time}에 초기화", resetTomorrowAt: "내일 {time}에 초기화", resetDateAt: "{date} {time}에 초기화", resetUnderMinute: "1분 이내 초기화", resetAfter: "{duration} 후 초기화", durationDay: "{count}일", durationHour: "{count}시간", durationMinute: "{count}분",
      refreshing: "새로 고치는 중…", notUpdated: "업데이트되지 않음", updatedNow: "방금 업데이트", updatedMinutes: "{count}분 전에 업데이트", updatedHours: "{count}시간 전에 업데이트", updatedAt: "{time}에 업데이트",
      usage: "사용량 {value}", resetOccurred: "초기화 감지됨", fullWindow: "전체 구간 ≈ {value}", windowUsage: "이 구간에서 {spent} tokens를 사용하고 {output}을 출력했습니다", reasoning: "(reasoning {value} 포함)", billingExplanation: ". 사용량은 한도에 실제 반영된 양이며 대부분 캐시 입력 재읽기입니다.", resetExplanation: "이 구간에서 한도 초기화가 감지되어 초기화 이후만 집계하며 전체 구간 추정은 하지 않습니다.", gapExplanation: "관측 공백 중 초기화가 있었을 수 있어 전체 구간 추정은 하지 않습니다.", estimateExplanation: "{used}% 사용을 기준으로 전체 구간은 약 {value} tokens입니다.", localOnlyExplanation: "사용량은 이 PC만 포함하고 한도 막대는 계정 전체를 나타냅니다.", noWindow: "해당 구간 없음",
      totalUsage: "누적 사용량", peakDaily: "일일 최대 사용량", totalOutput: "누적 출력", daysShort: "{count}일", currentStreak: "현재 연속", longestStreak: "최장 연속", tokenActivity: "Token 활동", yearlyActivity: "최근 1년 · {count}일 기록", localHeatHint: "이 PC만 집계 · 색이 진할수록 당일 Token 사용량이 많음",
      accountList: "계정 목록", currentLogin: "현재 로그인", unassignedInfo: "미할당 기록 ⓘ", unassignedUsage: "미할당 {value} Token ⓘ", unassignedHistory: "최근 30일간 이 PC의 기록 중 이 사용량을 발견된 계정에 연결할 공식 신원 이벤트가 충분하지 않습니다. 어떤 계정의 한도 카드에도 포함하지 않으며 추측하거나 채우지 않습니다.", grokHistory: "Sandglass는 이제 이 계정의 공식 한도, 초기화 시각, 이 PC 사용량 귀속을 계속 모니터링합니다. 관측 시작 전 신원을 확인할 수 없는 기록은 '미할당'으로 표시하며 추측하거나 채우지 않습니다.",
      allAccountsShown: "발견된 모든 계정이 개요에 표시됨", selectAccount: "추가할 계정 선택", addAccountAria: "{provider} {account} 추가", scanningTitle: "이 PC의 계정을 스캔하는 중…", scanningCopy: "Claude, Codex, Grok의 공식 로컬 로그인 정보를 읽기 전용으로 확인합니다. 최대 20초가 걸립니다.", scanning: "스캔 중", scanningEllipsis: "스캔 중…", scanComplete: "스캔 완료", foundAccounts: "인식 가능한 계정 {count}개 발견", chooseOverview: "개요에 표시할 계정을 선택하세요. 언제든 추가하거나 제거할 수 있습니다.", addAccount: "계정 추가", stillNoAccounts: "인식 가능한 계정을 아직 찾지 못함", noAccountsFound: "인식 가능한 계정을 찾지 못함", discoveryBoundary: "Sandglass는 공식 클라이언트로 로그인했고 이 PC에 기록이 남은 계정만 발견할 수 있습니다. 로그아웃 상태, 클라우드 전용 또는 아직 기록되지 않은 계정은 추론할 수 없습니다.", loginThenScan: "공식 Claude, Codex 또는 Grok 클라이언트에서 로그인해 한 번 사용한 다음 다시 스캔하세요.", rescan: "다시 스캔", scanFailed: "스캔을 완료하지 못함", serviceUnavailable: "로컬 서비스가 응답하지 않습니다. 잠시 후 다시 시도하세요.", onboardingTitle: "이 PC의 AI 계정 찾기", onboardingCopy: "Sandglass는 공식 클라이언트가 이 PC에 남긴 로그인 정보만 읽기 전용으로 스캔합니다. 자격 증명을 변경하거나 계정을 전환하지 않습니다.", scanAccounts: "로컬 계정 스캔", removeAccountAria: "개요에서 {provider} {account} 제거", addAccountButton: "＋ 계정 추가", refresh: "새로 고침",
      shownOnOverview: "표시 중", shownAccountAria: "{provider} {account} 계정은 개요에 이미 표시되어 있습니다",
      offlineTitle: "오프라인 상태로 보입니다", offlineCopy: "다시 연결한 뒤 새로 고치면 로컬 한도 데이터를 계속 사용할 수 있습니다.", quotaUnavailableTitle: "한도 정보를 일시적으로 읽을 수 없음", quotaUnavailableCopy: "연결을 확인하고 새로 고치세요. 캐시가 있으면 이전 데이터를 표시합니다.", notScannedTitle: "로컬 계정을 아직 스캔하지 않음", notScannedCopy: "개요로 돌아가 읽기 전용 스캔을 먼저 실행하세요.", noLocalLoginTitle: "로그인된 로컬 계정이 없음", noLocalLoginCopy: "Claude, Codex 또는 Grok에 로그인한 다음 새로 고치세요.", signedOut: "로그아웃됨", providerNoLoginTitle: "이 PC에 로그인 정보가 없음", providerNoLoginCopy: "공식 클라이언트에서 로그인한 다음 새로 고치세요.", foundNewAccounts: "새 계정 {count}개 발견. 추가할 계정을 선택하세요", noNewAccounts: "스캔 완료. 새 계정 없음", scanRetryNotice: "스캔을 완료하지 못했습니다. 잠시 후 다시 시도하세요", authExpired: "로그인이 만료되었습니다. 공식 클라이언트에서 다시 로그인하세요.", rateLimited: "요청이 제한되었습니다. 잠시 후 다시 시도하고 세션이 만료되었다면 공식 클라이언트에서 다시 로그인하세요.", autostartTitle: "로그인 시 자동 시작 권장", autostartCopy: "트레이에 계속 실행하면 계정 전환, 한도 변화, 초기화를 온전히 기록할 수 있습니다. Token은 나중에 다시 스캔할 수 있지만 일부 기록은 복원할 수 없습니다.", enableAutostart: "사용", later: "나중에", autostartFailed: "자동 시작을 켜지 못했습니다. 다시 시도하거나 트레이 메뉴에서 설정하세요.",
      telemetryReady: "정밀 모니터링 수신 준비 완료 ⓘ", telemetryReadyNote: "{provider} 공식 클라이언트의 OTLP 사용량 이벤트를 아직 받지 못했습니다. 기존 로컬 로그는 증명 가능한 범위와 미할당 사용량만 표시하며 현재 계정으로 과거를 채우지 않습니다.", telemetryUsageOnly: "계정 정보 없는 사용량 증거 수신 ⓘ", telemetryUsageOnlyNote: "{provider} 공식 사용량 이벤트를 받았지만 계정 정보가 없습니다. 최근 수신 시각은 {time}입니다. 미할당 상태로 유지되며 현재 합계에는 아직 포함하지 않습니다.", telemetryIdentity: "계정 증거 {count}건 수신 ⓘ", telemetryIdentityNote: "계정 정보를 직접 포함한 {provider} 공식 이벤트를 저장했습니다. 최근 수신 시각은 {time}입니다. 로컬 로그와의 중복을 막기 위해 현재 합계에는 아직 포함하지 않습니다."
    }
  };

  Object.assign(messages["zh-CN"], {refreshIn: "{seconds}秒后刷新"});
  Object.assign(messages["zh-TW"], {refreshIn: "{seconds}秒後重新整理"});
  Object.assign(messages["en-US"], {refreshIn: "Refresh in {seconds}s"});
  Object.assign(messages["es-ES"], {refreshIn: "Actualización en {seconds} s"});
  Object.assign(messages["fr-FR"], {refreshIn: "Actualisation dans {seconds} s"});
  Object.assign(messages["de-DE"], {refreshIn: "Aktualisierung in {seconds} s"});
  Object.assign(messages["pt-BR"], {refreshIn: "Atualização em {seconds} s"});
  Object.assign(messages["ru-RU"], {refreshIn: "Обновление через {seconds} с"});
  Object.assign(messages["ja-JP"], {refreshIn: "{seconds}秒後に更新"});
  Object.assign(messages["ko-KR"], {refreshIn: "{seconds}초 후 새로 고침"});
  Object.assign(messages["zh-CN"], {runtimeBlockedTitle: "Windows 已阻止界面组件", runtimeBlockedCopy: "应用控制策略拦截了 Sandglass 的界面组件。这不是账号掉线；请在 Windows 安全策略中允许已签名的 Sandglass 组件。", runtimeComponentTitle: "界面组件不可用", runtimeComponentCopy: "Sandglass 已回退到兼容界面。这不是账号掉线；运行 sandglass doctor 查看组件状态。"});
  Object.assign(messages["zh-TW"], {runtimeBlockedTitle: "Windows 已封鎖介面元件", runtimeBlockedCopy: "應用程式控制原則攔截了 Sandglass 的介面元件。這不是帳號中斷；請在 Windows 安全性原則中允許已簽署的 Sandglass 元件。", runtimeComponentTitle: "介面元件無法使用", runtimeComponentCopy: "Sandglass 已切換至相容介面。這不是帳號中斷；請執行 sandglass doctor 查看元件狀態。"});
  Object.assign(messages["en-US"], {runtimeBlockedTitle: "Windows blocked a UI component", runtimeBlockedCopy: "Application Control blocked a Sandglass UI component. Your provider account is not disconnected; allow the signed Sandglass component in Windows policy.", runtimeComponentTitle: "A UI component is unavailable", runtimeComponentCopy: "Sandglass is using its compatible fallback. Your provider account is not disconnected; run sandglass doctor for component status."});
  Object.assign(messages["es-ES"], {runtimeBlockedTitle: "Windows bloqueó un componente de interfaz", runtimeBlockedCopy: "Application Control bloqueó un componente de Sandglass. La cuenta no está desconectada; permite el componente firmado en la directiva de Windows.", runtimeComponentTitle: "Un componente de interfaz no está disponible", runtimeComponentCopy: "Sandglass usa la interfaz compatible. La cuenta no está desconectada; ejecuta sandglass doctor para ver el estado."});
  Object.assign(messages["fr-FR"], {runtimeBlockedTitle: "Windows a bloqué un composant d’interface", runtimeBlockedCopy: "Application Control a bloqué un composant Sandglass. Le compte n’est pas déconnecté ; autorisez le composant signé dans la stratégie Windows.", runtimeComponentTitle: "Un composant d’interface est indisponible", runtimeComponentCopy: "Sandglass utilise l’interface de secours compatible. Le compte n’est pas déconnecté ; lancez sandglass doctor pour voir l’état."});
  Object.assign(messages["de-DE"], {runtimeBlockedTitle: "Windows hat eine UI-Komponente blockiert", runtimeBlockedCopy: "Application Control hat eine Sandglass-Komponente blockiert. Das Konto ist nicht getrennt; lassen Sie die signierte Komponente in der Windows-Richtlinie zu.", runtimeComponentTitle: "Eine UI-Komponente ist nicht verfügbar", runtimeComponentCopy: "Sandglass verwendet die kompatible Ersatzoberfläche. Das Konto ist nicht getrennt; prüfen Sie den Status mit sandglass doctor."});
  Object.assign(messages["pt-BR"], {runtimeBlockedTitle: "O Windows bloqueou um componente da interface", runtimeBlockedCopy: "O Application Control bloqueou um componente do Sandglass. A conta não está desconectada; permita o componente assinado na política do Windows.", runtimeComponentTitle: "Um componente da interface está indisponível", runtimeComponentCopy: "O Sandglass está usando a interface compatível. A conta não está desconectada; execute sandglass doctor para ver o estado."});
  Object.assign(messages["ru-RU"], {runtimeBlockedTitle: "Windows заблокировала компонент интерфейса", runtimeBlockedCopy: "Application Control заблокировал компонент Sandglass. Учётная запись не отключена; разрешите подписанный компонент в политике Windows.", runtimeComponentTitle: "Компонент интерфейса недоступен", runtimeComponentCopy: "Sandglass использует совместимый резервный интерфейс. Учётная запись не отключена; выполните sandglass doctor для проверки."});
  Object.assign(messages["ja-JP"], {runtimeBlockedTitle: "Windows が UI コンポーネントをブロックしました", runtimeBlockedCopy: "Application Control が Sandglass のコンポーネントをブロックしました。アカウント切断ではありません。署名済みコンポーネントを Windows ポリシーで許可してください。", runtimeComponentTitle: "UI コンポーネントを利用できません", runtimeComponentCopy: "Sandglass は互換 UI に切り替えました。アカウント切断ではありません。sandglass doctor で状態を確認してください。"});
  Object.assign(messages["ko-KR"], {runtimeBlockedTitle: "Windows가 UI 구성 요소를 차단했습니다", runtimeBlockedCopy: "Application Control이 Sandglass 구성 요소를 차단했습니다. 계정 연결이 끊긴 것이 아닙니다. Windows 정책에서 서명된 구성 요소를 허용하세요.", runtimeComponentTitle: "UI 구성 요소를 사용할 수 없습니다", runtimeComponentCopy: "Sandglass가 호환 인터페이스로 전환했습니다. 계정 연결이 끊긴 것이 아닙니다. sandglass doctor로 상태를 확인하세요."});
  Object.assign(messages["zh-CN"], {
    telemetryReady: "尚未收到精确监测事件 ⓘ",
    telemetryWaiting: "接收器已开启，等待 {provider} 事件 ⓘ",
    telemetryReadyNote: "尚未收到 {provider} 官方客户端的 OTLP 事件，因此目前无法证明客户端是否已启用精确监测。点击可查看只影响当次官方客户端进程的启用方式。",
    telemetryTraffic: "已连接，等待用量事件 ⓘ",
    telemetryTrafficNote: "最近于 {time} 收到 {provider} 官方客户端事件，但还没有可用的 Token 事件。连接已建立，当前总量没有变化。",
    telemetrySetupTitle: "为 {provider} 开启精确监测",
    telemetrySetupIntro: "复制到 PowerShell 运行。命令显式关闭厂商提供的内容开关；Sandglass 不修改厂商配置，且只保存账号、会话、时间、模型和 Token 字段。",
    telemetryRestart: "先退出正在运行的该客户端；命令只影响由它启动的新进程。精确事件会先作为影子证据核对，不会重复计入总量。",
    telemetryCopy: "复制命令",
    telemetryCopied: "已复制",
    telemetryCheck: "重新检测", telemetryEnable: "开启", telemetryEnabling: "开启中…"
  });
  Object.assign(messages["zh-TW"], {
    telemetryReady: "尚未收到精確監測事件 ⓘ",
    telemetryWaiting: "接收器已啟用，等待 {provider} 事件 ⓘ",
    telemetryReadyNote: "尚未收到 {provider} 官方用戶端的 OTLP 事件，因此目前無法證明用戶端是否已啟用精確監測。點擊可查看只影響當次官方用戶端程序的啟用方式。",
    telemetryTraffic: "已連線，等待用量事件 ⓘ",
    telemetryTrafficNote: "最近於 {time} 收到 {provider} 官方用戶端事件，但尚無可用的 Token 事件。連線已建立，目前總量沒有變化。",
    telemetrySetupTitle: "為 {provider} 啟用精確監測",
    telemetrySetupIntro: "複製到 PowerShell 執行。Sandglass 不修改廠商設定，也不接收提示詞、工具內容或檔案路徑。",
    telemetryRestart: "請先退出正在執行的用戶端；命令只影響由它啟動的新程序。精確事件會先作為影子證據核對，不會重複計入總量。",
    telemetryCopy: "複製命令", telemetryCopied: "已複製", telemetryCheck: "重新檢測", telemetryEnable: "啟用", telemetryEnabling: "啟用中…"
  });
  Object.assign(messages["en-US"], {
    telemetryReady: "No precise monitoring events yet ⓘ",
    telemetryWaiting: "Receiver is on; waiting for {provider} events ⓘ",
    telemetryReadyNote: "No official {provider} OTLP event has arrived, so Sandglass cannot yet prove whether precise monitoring is enabled. Click for a one-process setup command.",
    telemetryTraffic: "Connected; waiting for usage ⓘ",
    telemetryTrafficNote: "An official {provider} client event arrived at {time}, but no supported token event has arrived yet. The connection is live and current totals are unchanged.",
    telemetrySetupTitle: "Enable precise {provider} monitoring",
    telemetrySetupIntro: "Copy this into PowerShell. Sandglass does not change vendor configuration or request prompts, tool content, or file paths.",
    telemetryRestart: "Exit the running client first. This affects only the new process it launches. Precise events remain shadow evidence until matched, so totals are not duplicated.",
    telemetryCopy: "Copy command", telemetryCopied: "Copied", telemetryCheck: "Check again", telemetryEnable: "Enable", telemetryEnabling: "Enabling…"
  });
  Object.assign(messages["es-ES"], {
    telemetryReady: "Aún no hay eventos de supervisión precisa ⓘ",
    telemetryWaiting: "Receptor activo; esperando eventos de {provider} ⓘ",
    telemetryReadyNote: "No ha llegado ningún evento OTLP oficial de {provider}; Sandglass aún no puede confirmar si está activado. Haz clic para ver el comando de una sola sesión.",
    telemetryTraffic: "Conectado; esperando uso ⓘ",
    telemetryTrafficNote: "Llegó un evento oficial de {provider} a las {time}, pero aún no hay un evento de tokens compatible. Los totales no cambian.",
    telemetrySetupTitle: "Activar la supervisión precisa de {provider}",
    telemetrySetupIntro: "Copia esto en PowerShell. Sandglass no cambia la configuración ni solicita prompts, contenido de herramientas o rutas.",
    telemetryRestart: "Cierra primero el cliente activo. Solo afecta al nuevo proceso y los eventos se contrastan en modo sombra para no duplicar totales.",
    telemetryCopy: "Copiar comando", telemetryCopied: "Copiado", telemetryCheck: "Comprobar", telemetryEnable: "Activar", telemetryEnabling: "Activando…"
  });
  Object.assign(messages["fr-FR"], {
    telemetryReady: "Aucun événement de suivi précis ⓘ",
    telemetryWaiting: "Récepteur activé ; en attente des événements {provider} ⓘ",
    telemetryReadyNote: "Aucun événement OTLP officiel de {provider} n’est arrivé ; Sandglass ne peut donc pas encore confirmer l’activation. Cliquez pour afficher la commande limitée à un processus.",
    telemetryTraffic: "Connecté, en attente d’utilisation ⓘ",
    telemetryTrafficNote: "Un événement officiel de {provider} est arrivé à {time}, mais aucun événement de tokens compatible. Les totaux restent inchangés.",
    telemetrySetupTitle: "Activer le suivi précis de {provider}",
    telemetrySetupIntro: "Copiez ceci dans PowerShell. Sandglass ne modifie aucune configuration et ne demande ni prompts, ni contenu d’outil, ni chemins.",
    telemetryRestart: "Fermez d’abord le client actif. Seul le nouveau processus est concerné ; les événements restent en comparaison parallèle pour éviter tout doublon.",
    telemetryCopy: "Copier", telemetryCopied: "Copié", telemetryCheck: "Revérifier", telemetryEnable: "Activer", telemetryEnabling: "Activation…"
  });
  Object.assign(messages["de-DE"], {
    telemetryReady: "Noch keine präzisen Messereignisse ⓘ",
    telemetryWaiting: "Empfänger aktiv; warte auf {provider}-Ereignisse ⓘ",
    telemetryReadyNote: "Es ist noch kein offizielles OTLP-Ereignis von {provider} eingetroffen; die Aktivierung ist daher nicht belegt. Klicken Sie für einen einmaligen Startbefehl.",
    telemetryTraffic: "Verbunden; warte auf Nutzung ⓘ",
    telemetryTrafficNote: "Um {time} traf ein offizielles {provider}-Ereignis ein, aber noch kein unterstütztes Token-Ereignis. Die Summen bleiben unverändert.",
    telemetrySetupTitle: "Präzise {provider}-Messung aktivieren",
    telemetrySetupIntro: "In PowerShell kopieren. Sandglass ändert keine Anbieterkonfiguration und fordert keine Prompts, Werkzeuginhalte oder Pfade an.",
    telemetryRestart: "Zuerst den laufenden Client beenden. Nur der neu gestartete Prozess ist betroffen; Ereignisse bleiben zur Doppelzählungsprüfung im Schattenmodus.",
    telemetryCopy: "Befehl kopieren", telemetryCopied: "Kopiert", telemetryCheck: "Erneut prüfen", telemetryEnable: "Aktivieren", telemetryEnabling: "Wird aktiviert…"
  });
  Object.assign(messages["pt-BR"], {
    telemetryReady: "Ainda sem eventos de monitoramento preciso ⓘ",
    telemetryWaiting: "Receptor ativo; aguardando eventos de {provider} ⓘ",
    telemetryReadyNote: "Nenhum evento OTLP oficial do {provider} chegou; o Sandglass ainda não pode confirmar a ativação. Clique para ver o comando de um único processo.",
    telemetryTraffic: "Conectado; aguardando uso ⓘ",
    telemetryTrafficNote: "Um evento oficial do {provider} chegou às {time}, mas ainda não há evento de tokens compatível. Os totais não mudam.",
    telemetrySetupTitle: "Ativar monitoramento preciso do {provider}",
    telemetrySetupIntro: "Copie no PowerShell. O Sandglass não altera configurações nem solicita prompts, conteúdo de ferramentas ou caminhos.",
    telemetryRestart: "Feche primeiro o cliente em execução. Só o novo processo é afetado; os eventos ficam em modo sombra para evitar duplicação.",
    telemetryCopy: "Copiar comando", telemetryCopied: "Copiado", telemetryCheck: "Verificar", telemetryEnable: "Ativar", telemetryEnabling: "Ativando…"
  });
  Object.assign(messages["ru-RU"], {
    telemetryReady: "Точных событий мониторинга пока нет ⓘ",
    telemetryWaiting: "Приёмник включён; ожидаются события {provider} ⓘ",
    telemetryReadyNote: "Официальные события OTLP от {provider} ещё не поступали, поэтому включение пока не подтверждено. Нажмите, чтобы увидеть команду для одного процесса.",
    telemetryTraffic: "Подключено; ожидается расход ⓘ",
    telemetryTrafficNote: "В {time} поступило официальное событие {provider}, но ещё нет поддерживаемого события токенов. Итоги не изменены.",
    telemetrySetupTitle: "Включить точный мониторинг {provider}",
    telemetrySetupIntro: "Скопируйте в PowerShell. Sandglass не меняет настройки и не запрашивает запросы, содержимое инструментов или пути.",
    telemetryRestart: "Сначала закройте работающий клиент. Команда влияет только на новый процесс; события сверяются в теневом режиме без двойного учёта.",
    telemetryCopy: "Копировать", telemetryCopied: "Скопировано", telemetryCheck: "Проверить", telemetryEnable: "Включить", telemetryEnabling: "Включение…"
  });
  Object.assign(messages["ja-JP"], {
    telemetryReady: "精密監視イベントはまだありません ⓘ",
    telemetryWaiting: "受信口は有効です。{provider} のイベントを待機中 ⓘ",
    telemetryReadyNote: "{provider} 公式クライアントの OTLP イベントが未着のため、有効化済みか確認できません。クリックすると1プロセスだけの起動コマンドを表示します。",
    telemetryTraffic: "接続済み・使用量待ち ⓘ",
    telemetryTrafficNote: "{time} に {provider} 公式イベントを受信しましたが、対応する Token イベントはまだありません。現在の合計は変わりません。",
    telemetrySetupTitle: "{provider} の精密監視を有効化",
    telemetrySetupIntro: "PowerShell にコピーしてください。Sandglass は設定を変更せず、プロンプト、ツール内容、ファイルパスを要求しません。",
    telemetryRestart: "先に実行中のクライアントを終了してください。新しいプロセスだけに適用され、イベントは重複防止のためシャドー照合されます。",
    telemetryCopy: "コマンドをコピー", telemetryCopied: "コピー済み", telemetryCheck: "再確認", telemetryEnable: "有効化", telemetryEnabling: "有効化中…"
  });
  Object.assign(messages["ko-KR"], {
    telemetryReady: "정밀 모니터링 이벤트 없음 ⓘ",
    telemetryWaiting: "수신기는 켜져 있으며 {provider} 이벤트를 기다리는 중 ⓘ",
    telemetryReadyNote: "{provider} 공식 클라이언트 OTLP 이벤트가 아직 없어 활성화 여부를 확인할 수 없습니다. 클릭하면 한 프로세스용 실행 명령을 볼 수 있습니다.",
    telemetryTraffic: "연결됨 · 사용량 대기 중 ⓘ",
    telemetryTrafficNote: "{time}에 {provider} 공식 이벤트를 받았지만 지원되는 Token 이벤트는 아직 없습니다. 현재 합계는 변하지 않습니다.",
    telemetrySetupTitle: "{provider} 정밀 모니터링 켜기",
    telemetrySetupIntro: "PowerShell에 복사하세요. Sandglass는 설정을 바꾸지 않으며 프롬프트, 도구 내용, 파일 경로를 요청하지 않습니다.",
    telemetryRestart: "실행 중인 클라이언트를 먼저 종료하세요. 새 프로세스에만 적용되며 이벤트는 중복 방지를 위해 섀도우 대조됩니다.",
    telemetryCopy: "명령 복사", telemetryCopied: "복사됨", telemetryCheck: "다시 확인", telemetryEnable: "켜기", telemetryEnabling: "켜는 중…"
  });

  Object.assign(messages["zh-CN"], {telemetryReceiverOff: "精确监测尚未开启", telemetryReceiverOffNote: "点击“开启”后，Sandglass 会在本机开启只接收官方 OTLP 事件的端口，再显示启动客户端所需的命令。不会开放账号、额度或报告接口。", telemetryReceiverError: "精确监测端口无法启动 ⓘ", telemetryReceiverErrorNote: "本机 7740 端口被占用或无法监听，因此没有复制无效命令。请关闭占用程序后重试。"});
  Object.assign(messages["zh-TW"], {telemetryReceiverOff: "精確監測尚未啟用", telemetryReceiverOffNote: "點擊「啟用」後，Sandglass 會在本機開啟只接收官方 OTLP 事件的連接埠，再顯示啟動用戶端所需的命令。不會開放帳號、額度或報告介面。", telemetryReceiverError: "精確監測連接埠無法啟動 ⓘ", telemetryReceiverErrorNote: "本機 7740 連接埠被佔用或無法監聽，因此未複製無效命令。請關閉佔用程式後重試。"});
  Object.assign(messages["en-US"], {telemetryReceiverOff: "Precise monitoring is off", telemetryReceiverOffNote: "Select Enable to open a local receiver for official OTLP events, then show the command needed to start the client. Account, quota, and report APIs remain closed.", telemetryReceiverError: "Precise monitoring could not start ⓘ", telemetryReceiverErrorNote: "Local port 7740 is unavailable, so Sandglass did not copy a command that could not work. Free the port and try again."});
  Object.assign(messages["es-ES"], {telemetryReceiverOff: "El monitoreo preciso está desactivado", telemetryReceiverOffNote: "Selecciona Activar para abrir un receptor local de eventos OTLP oficiales y mostrar el comando necesario para iniciar el cliente. No expone cuentas, cuotas ni informes.", telemetryReceiverError: "No se pudo iniciar el monitoreo preciso ⓘ", telemetryReceiverErrorNote: "El puerto local 7740 no está disponible. Libéralo y vuelve a intentarlo."});
  Object.assign(messages["fr-FR"], {telemetryReceiverOff: "Le suivi précis est désactivé", telemetryReceiverOffNote: "Cliquez sur Activer pour ouvrir un récepteur local réservé aux événements OTLP officiels, puis afficher la commande de lancement du client. Aucun accès aux comptes, quotas ou rapports n’est exposé.", telemetryReceiverError: "Le suivi précis n’a pas pu démarrer ⓘ", telemetryReceiverErrorNote: "Le port local 7740 est indisponible. Libérez-le puis réessayez."});
  Object.assign(messages["de-DE"], {telemetryReceiverOff: "Präzise Messung ist aus", telemetryReceiverOffNote: "Wählen Sie Aktivieren, um einen lokalen Empfänger für offizielle OTLP-Ereignisse zu öffnen und danach den Startbefehl für den Client anzuzeigen. Konto-, Kontingent- und Bericht-APIs bleiben geschlossen.", telemetryReceiverError: "Präzise Messung konnte nicht starten ⓘ", telemetryReceiverErrorNote: "Der lokale Port 7740 ist nicht verfügbar. Geben Sie ihn frei und versuchen Sie es erneut."});
  Object.assign(messages["pt-BR"], {telemetryReceiverOff: "O monitoramento preciso está desativado", telemetryReceiverOffNote: "Selecione Ativar para abrir um receptor local somente para eventos OTLP oficiais e mostrar o comando necessário para iniciar o cliente. Contas, cotas e relatórios não são expostos.", telemetryReceiverError: "Não foi possível iniciar o monitoramento preciso ⓘ", telemetryReceiverErrorNote: "A porta local 7740 não está disponível. Libere-a e tente novamente."});
  Object.assign(messages["ru-RU"], {telemetryReceiverOff: "Точный мониторинг выключен", telemetryReceiverOffNote: "Нажмите «Включить», чтобы открыть локальный приёмник официальных событий OTLP и показать команду запуска клиента. API аккаунтов, квот и отчётов не открываются.", telemetryReceiverError: "Не удалось запустить точный мониторинг ⓘ", telemetryReceiverErrorNote: "Локальный порт 7740 недоступен. Освободите его и повторите попытку."});
  Object.assign(messages["ja-JP"], {telemetryReceiverOff: "精密監視はオフです", telemetryReceiverOffNote: "有効化を選ぶと、公式 OTLP イベント専用のローカル受信口を開き、クライアントの起動に必要なコマンドを表示します。アカウント、クォータ、レポート API は公開しません。", telemetryReceiverError: "精密監視を開始できません ⓘ", telemetryReceiverErrorNote: "ローカルポート 7740 を使用できません。使用中のプロセスを終了して再試行してください。"});
  Object.assign(messages["ko-KR"], {telemetryReceiverOff: "정밀 모니터링이 꺼져 있음", telemetryReceiverOffNote: "켜기를 선택하면 공식 OTLP 이벤트만 받는 로컬 수신기를 열고 클라이언트 시작에 필요한 명령을 표시합니다. 계정, 할당량, 보고서 API는 공개하지 않습니다.", telemetryReceiverError: "정밀 모니터링을 시작할 수 없음 ⓘ", telemetryReceiverErrorNote: "로컬 7740 포트를 사용할 수 없습니다. 점유 프로세스를 종료한 뒤 다시 시도하세요."});
  Object.assign(messages["zh-CN"], {telemetryProviderOff: "该平台精确监测未开启", telemetryProviderOffNote: "本机接收器可以运行，但尚未开启 {provider} 客户端。点击“开启”只配置这个平台，不会影响其他平台。"});
  Object.assign(messages["zh-TW"], {telemetryProviderOff: "此平台精確監測尚未啟用", telemetryProviderOffNote: "本機接收器可以運行，但尚未啟用 {provider} 用戶端。點擊「啟用」只設定這個平台，不會影響其他平台。"});
  Object.assign(messages["en-US"], {telemetryProviderOff: "This platform is off", telemetryProviderOffNote: "The local receiver may be running, but the {provider} client is not enabled. Enable this platform only; other platforms are unchanged."});
  Object.assign(messages["es-ES"], {telemetryProviderOff: "Esta plataforma está desactivada", telemetryProviderOffNote: "El receptor local puede estar activo, pero el cliente {provider} no está habilitado. Activa solo esta plataforma; las demás no cambian."});
  Object.assign(messages["fr-FR"], {telemetryProviderOff: "Cette plateforme est désactivée", telemetryProviderOffNote: "Le récepteur local peut fonctionner, mais le client {provider} n’est pas activé. Activez uniquement cette plateforme ; les autres ne changent pas."});
  Object.assign(messages["de-DE"], {telemetryProviderOff: "Diese Plattform ist deaktiviert", telemetryProviderOffNote: "Der lokale Empfänger kann laufen, aber der {provider}-Client ist nicht aktiviert. Aktivieren Sie nur diese Plattform; andere bleiben unverändert."});
  Object.assign(messages["pt-BR"], {telemetryProviderOff: "Esta plataforma está desativada", telemetryProviderOffNote: "O receptor local pode estar ativo, mas o cliente {provider} não está habilitado. Ative somente esta plataforma; as outras não mudam."});
  Object.assign(messages["ru-RU"], {telemetryProviderOff: "Эта платформа выключена", telemetryProviderOffNote: "Локальный приёмник может работать, но клиент {provider} не включён. Включается только эта платформа; остальные не меняются."});
  Object.assign(messages["ja-JP"], {telemetryProviderOff: "このプラットフォームはオフです", telemetryProviderOffNote: "ローカル受信口は動作していても、{provider} クライアントは有効になっていません。「有効化」はこのプラットフォームだけに適用されます。"});
  Object.assign(messages["ko-KR"], {telemetryProviderOff: "이 플랫폼은 꺼져 있음", telemetryProviderOffNote: "로컬 수신기가 실행 중이어도 {provider} 클라이언트는 켜지지 않았습니다. 켜기는 이 플랫폼에만 적용되며 다른 플랫폼은 바뀌지 않습니다."});
  Object.assign(messages["zh-CN"], {telemetryProviderEnabled: "已开启 {provider} 精确监测，等待事件 ⓘ"});
  Object.assign(messages["zh-TW"], {telemetryProviderEnabled: "已啟用 {provider} 精確監測，等待事件 ⓘ"});
  Object.assign(messages["en-US"], {telemetryProviderEnabled: "{provider} monitoring is on; waiting for events ⓘ"});
  Object.assign(messages["es-ES"], {telemetryProviderEnabled: "Monitoreo de {provider} activado; esperando eventos ⓘ"});
  Object.assign(messages["fr-FR"], {telemetryProviderEnabled: "Suivi de {provider} activé ; en attente d’événements ⓘ"});
  Object.assign(messages["de-DE"], {telemetryProviderEnabled: "{provider}-Überwachung aktiv; warte auf Ereignisse ⓘ"});
  Object.assign(messages["pt-BR"], {telemetryProviderEnabled: "Monitoramento de {provider} ativo; aguardando eventos ⓘ"});
  Object.assign(messages["ru-RU"], {telemetryProviderEnabled: "Мониторинг {provider} включён; ожидаются события ⓘ"});
  Object.assign(messages["ja-JP"], {telemetryProviderEnabled: "{provider} の監視はオン。イベント待機中 ⓘ"});
  Object.assign(messages["ko-KR"], {telemetryProviderEnabled: "{provider} 모니터링 켜짐; 이벤트 대기 중 ⓘ"});
  Object.assign(messages["zh-CN"], {telemetryDisableProvider: "关闭该平台监测"});
  Object.assign(messages["zh-TW"], {telemetryDisableProvider: "關閉此平台監測"});
  Object.assign(messages["en-US"], {telemetryDisableProvider: "Turn this platform off"});
  Object.assign(messages["es-ES"], {telemetryDisableProvider: "Desactivar esta plataforma"});
  Object.assign(messages["fr-FR"], {telemetryDisableProvider: "Désactiver cette plateforme"});
  Object.assign(messages["de-DE"], {telemetryDisableProvider: "Diese Plattform ausschalten"});
  Object.assign(messages["pt-BR"], {telemetryDisableProvider: "Desativar esta plataforma"});
  Object.assign(messages["ru-RU"], {telemetryDisableProvider: "Выключить эту платформу"});
  Object.assign(messages["ja-JP"], {telemetryDisableProvider: "このプラットフォームをオフ"});
  Object.assign(messages["ko-KR"], {telemetryDisableProvider: "이 플랫폼 끄기"});
  Object.assign(messages["zh-CN"], {telemetryDisable: "关闭接收器"});
  Object.assign(messages["zh-TW"], {telemetryDisable: "關閉接收器"});
  Object.assign(messages["en-US"], {telemetryDisable: "Turn receiver off"});
  Object.assign(messages["es-ES"], {telemetryDisable: "Desactivar receptor"});
  Object.assign(messages["fr-FR"], {telemetryDisable: "Désactiver le récepteur"});
  Object.assign(messages["de-DE"], {telemetryDisable: "Empfänger ausschalten"});
  Object.assign(messages["pt-BR"], {telemetryDisable: "Desativar receptor"});
  Object.assign(messages["ru-RU"], {telemetryDisable: "Выключить приёмник"});
  Object.assign(messages["ja-JP"], {telemetryDisable: "受信を停止"});
  Object.assign(messages["ko-KR"], {telemetryDisable: "수신기 끄기"});

  Object.assign(messages["zh-CN"], {
    help: "帮助", back: "返回", helpTitle: "帮助",
    helpStayLocalTitle: "数据留在本机", helpStayLocalCopy: "用量、账号和额度只写在 Sandglass 自己的目录，不会上传。",
    helpReadOnlyTitle: "不改你的账号", helpReadOnlyCopy: "不切换登录、不刷新凭据、不改 Claude、Codex、Grok 的文件。",
    helpCoverageTitle: "只展示能证明的", helpCoverageCopy: "只统计这台电脑上官方客户端写下的用量；证明不了的标成未归属，额度条则可能含其他设备。",
    helpBillingTitle: "不是账单", helpBillingCopy: "用来看本机活动和官方余量，不能对费用或发票。",
    helpAdvancedTitle: "复杂场景", helpAdvancedCopy: "把指令交给本机编码 agent",
    helpAdvancedNext: "粘贴到 Claude Code、Codex、Cursor 里运行",
    helpSkillAgentPrompt: "从 https://github.com/taiyun668/Sandglass 拉取 skills/sandglass-adapter/ 的完整目录，完整读取并执行其中的 Sandglass Adapter Skill。",
    helpSkillCopy: "复制给 Agent", helpSkillCopied: "已复制", userAdapterShort: "用户适配器验证", userAdapterVerified: "用户适配器验证：{sources}",
    customSourceRecords: "{count} 条证据", customSourceMirror: "{count} 条 · 认识 {recognized} 个字段 · 未知 {unknown} 个字段"
  });
  Object.assign(messages["zh-TW"], {
    help: "說明", back: "返回", helpTitle: "說明",
    helpStayLocalTitle: "資料留在本機", helpStayLocalCopy: "用量、帳號和額度只寫在 Sandglass 自己的目錄，不會上傳。",
    helpReadOnlyTitle: "不改你的帳號", helpReadOnlyCopy: "不切換登入、不刷新憑據、不改 Claude、Codex、Grok 的檔案。",
    helpCoverageTitle: "只顯示能證明的", helpCoverageCopy: "只統計這台電腦上官方用戶端寫下的用量；證明不了的標成未歸屬，額度列則可能含其他裝置。",
    helpBillingTitle: "不是帳單", helpBillingCopy: "用來看本機活動與官方餘量，不能對費用或發票。",
    helpAdvancedTitle: "複雜情境", helpAdvancedCopy: "把指令交給本機編碼 agent",
    helpAdvancedNext: "貼到 Claude Code、Codex、Cursor 裡執行",
    helpSkillAgentPrompt: "從 https://github.com/taiyun668/Sandglass 取得 skills/sandglass-adapter/ 的完整目錄，完整讀取並執行其中的 Sandglass Adapter Skill。",
    helpSkillCopy: "複製給 Agent", helpSkillCopied: "已複製", userAdapterShort: "使用者轉接器驗證", userAdapterVerified: "使用者轉接器驗證：{sources}",
    customSourceRecords: "{count} 筆證據", customSourceMirror: "{count} 筆 · 已辨識 {recognized} 個欄位 · 未知 {unknown} 個欄位"
  });
  Object.assign(messages["en-US"], {
    help: "Help", back: "Back", helpTitle: "Help",
    helpStayLocalTitle: "Stays on this computer", helpStayLocalCopy: "Usage, accounts, and quota are written only in Sandglass’s own folder and are never uploaded.",
    helpReadOnlyTitle: "Leaves your accounts alone", helpReadOnlyCopy: "It does not switch logins, refresh credentials, or change Claude, Codex, or Grok files.",
    helpCoverageTitle: "Shows only what it can prove", helpCoverageCopy: "It counts official usage written on this computer; unproven usage stays unassigned, and quota bars may include other devices.",
    helpBillingTitle: "Not a bill", helpBillingCopy: "These figures explain local activity and official remaining quota, not costs or invoices.",
    helpAdvancedTitle: "Complex scenarios", helpAdvancedCopy: "Give the instruction to a local coding agent.",
    helpAdvancedNext: "Paste it into Claude Code, Codex, or Cursor and run it.",
    helpSkillAgentPrompt: "Fetch the complete skills/sandglass-adapter/ directory from https://github.com/taiyun668/Sandglass, read it fully, and execute the Sandglass Adapter Skill.",
    helpSkillCopy: "Copy for agent", helpSkillCopied: "Copied", userAdapterShort: "User adapter verified", userAdapterVerified: "User adapter verified: {sources}",
    customSourceRecords: "{count} evidence records", customSourceMirror: "{count} receipts · {recognized} recognized fields · {unknown} unknown fields"
  });
  Object.assign(messages["es-ES"], {
    help: "Ayuda", back: "Volver", helpTitle: "Ayuda",
    helpStayLocalTitle: "Se queda en este equipo", helpStayLocalCopy: "El uso, las cuentas y la cuota se guardan solo en la carpeta de Sandglass y no se suben.",
    helpReadOnlyTitle: "No toca tus cuentas", helpReadOnlyCopy: "No cambia el inicio de sesión, no renueva credenciales ni modifica archivos de Claude, Codex o Grok.",
    helpCoverageTitle: "Solo muestra lo demostrable", helpCoverageCopy: "Cuenta el uso oficial escrito en este equipo; lo no demostrable queda sin asignar, y las barras de cuota pueden incluir otros dispositivos.",
    helpBillingTitle: "No es una factura", helpBillingCopy: "Sirven para ver la actividad local y la cuota oficial, no para conciliar costes o facturas.",
    helpAdvancedTitle: "Escenarios complejos", helpAdvancedCopy: "Pasa la instrucción a un agente local.",
    helpAdvancedNext: "Pégalo en Claude Code, Codex o Cursor y ejecútalo.",
    helpSkillAgentPrompt: "Obtén el directorio completo skills/sandglass-adapter/ desde https://github.com/taiyun668/Sandglass, léelo por completo y ejecuta Sandglass Adapter Skill.",
    helpSkillCopy: "Copiar para el agente", helpSkillCopied: "Copiado", userAdapterShort: "Adaptador verificado", userAdapterVerified: "Adaptador del usuario verificado: {sources}",
    customSourceRecords: "{count} registros de evidencia", customSourceMirror: "{count} entradas · {recognized} campos reconocidos · {unknown} desconocidos"
  });
  Object.assign(messages["fr-FR"], {
    help: "Aide", back: "Retour", helpTitle: "Aide",
    helpStayLocalTitle: "Reste sur cet ordinateur", helpStayLocalCopy: "L’usage, les comptes et les quotas ne sont écrits que dans le dossier de Sandglass et ne sont jamais envoyés.",
    helpReadOnlyTitle: "Ne touche pas à vos comptes", helpReadOnlyCopy: "Il ne change pas de connexion, ne renouvelle pas les identifiants et ne modifie pas les fichiers Claude, Codex ou Grok.",
    helpCoverageTitle: "N’affiche que ce qui est prouvé", helpCoverageCopy: "Il compte l’usage officiel écrit ici ; le reste reste non attribué, et les barres de quota peuvent inclure d’autres appareils.",
    helpBillingTitle: "Ce n’est pas une facture", helpBillingCopy: "Ces chiffres montrent l’activité locale et le quota officiel, pas les coûts ni les factures.",
    helpAdvancedTitle: "Scénarios complexes", helpAdvancedCopy: "Donnez l’instruction à un agent local.",
    helpAdvancedNext: "Collez-le dans Claude Code, Codex ou Cursor et lancez-le.",
    helpSkillAgentPrompt: "Récupère le dossier complet skills/sandglass-adapter/ depuis https://github.com/taiyun668/Sandglass, lis-le entièrement puis exécute Sandglass Adapter Skill.",
    helpSkillCopy: "Copier pour l’agent", helpSkillCopied: "Copié", userAdapterShort: "Adaptateur vérifié", userAdapterVerified: "Adaptateur utilisateur vérifié : {sources}",
    customSourceRecords: "{count} éléments de preuve", customSourceMirror: "{count} reçus · {recognized} champs reconnus · {unknown} inconnus"
  });
  Object.assign(messages["de-DE"], {
    help: "Hilfe", back: "Zurück", helpTitle: "Hilfe",
    helpStayLocalTitle: "Bleibt auf diesem Rechner", helpStayLocalCopy: "Nutzung, Konten und Kontingente liegen nur im Sandglass-Ordner und werden nicht hochgeladen.",
    helpReadOnlyTitle: "Lässt deine Konten unberührt", helpReadOnlyCopy: "Es wechselt keine Anmeldung, erneuert keine Zugangsdaten und ändert keine Claude-, Codex- oder Grok-Dateien.",
    helpCoverageTitle: "Zeigt nur Belegbares", helpCoverageCopy: "Es zählt offizielle Nutzung auf diesem Rechner; Unbelegtes bleibt unzugeordnet, Kontingentbalken können andere Geräte enthalten.",
    helpBillingTitle: "Keine Rechnung", helpBillingCopy: "Sie erklären lokale Aktivität und offizielle Restkontingente, nicht Kosten oder Rechnungen.",
    helpAdvancedTitle: "Komplexe Szenarien", helpAdvancedCopy: "Gib die Anweisung an einen lokalen Agenten.",
    helpAdvancedNext: "In Claude Code, Codex oder Cursor einfügen und ausführen.",
    helpSkillAgentPrompt: "Lade das vollständige Verzeichnis skills/sandglass-adapter/ von https://github.com/taiyun668/Sandglass, lies es vollständig und führe den Sandglass Adapter Skill aus.",
    helpSkillCopy: "Für Agent kopieren", helpSkillCopied: "Kopiert", userAdapterShort: "Nutzeradapter geprüft", userAdapterVerified: "Nutzeradapter geprüft: {sources}",
    customSourceRecords: "{count} Nachweise", customSourceMirror: "{count} Eingänge · {recognized} erkannte Felder · {unknown} unbekannte"
  });
  Object.assign(messages["pt-BR"], {
    help: "Ajuda", back: "Voltar", helpTitle: "Ajuda",
    helpStayLocalTitle: "Fica neste computador", helpStayLocalCopy: "Uso, contas e cotas são gravados só na pasta do Sandglass e nunca são enviados.",
    helpReadOnlyTitle: "Não mexe nas suas contas", helpReadOnlyCopy: "Não troca o login, não renova credenciais e não altera arquivos do Claude, Codex ou Grok.",
    helpCoverageTitle: "Mostra só o que dá para provar", helpCoverageCopy: "Conta o uso oficial gravado neste computador; o que não der para provar fica sem atribuição, e as barras de cota podem incluir outros dispositivos.",
    helpBillingTitle: "Não é uma fatura", helpBillingCopy: "Servem para ver a atividade local e a cota oficial, não para conferir custos ou faturas.",
    helpAdvancedTitle: "Cenários complexos", helpAdvancedCopy: "Passe a instrução a um agente local.",
    helpAdvancedNext: "Cole no Claude Code, Codex ou Cursor e execute.",
    helpSkillAgentPrompt: "Baixe o diretório completo skills/sandglass-adapter/ de https://github.com/taiyun668/Sandglass, leia tudo e execute Sandglass Adapter Skill.",
    helpSkillCopy: "Copiar para o agente", helpSkillCopied: "Copiado", userAdapterShort: "Adaptador verificado", userAdapterVerified: "Adaptador do usuário verificado: {sources}",
    customSourceRecords: "{count} registros de evidência", customSourceMirror: "{count} entradas · {recognized} campos reconhecidos · {unknown} desconhecidos"
  });
  Object.assign(messages["ru-RU"], {
    help: "Справка", back: "Назад", helpTitle: "Справка",
    helpStayLocalTitle: "Остаётся на этом компьютере", helpStayLocalCopy: "Расход, аккаунты и квоты пишутся только в папку Sandglass и никуда не загружаются.",
    helpReadOnlyTitle: "Не трогает ваши аккаунты", helpReadOnlyCopy: "Не переключает вход, не обновляет учётные данные и не меняет файлы Claude, Codex и Grok.",
    helpCoverageTitle: "Показывает только доказуемое", helpCoverageCopy: "Считает официальный расход, записанный на этом компьютере; недоказуемое остаётся нераспределённым, а полосы квот могут включать другие устройства.",
    helpBillingTitle: "Это не счёт", helpBillingCopy: "Эти цифры показывают локальную активность и официальный остаток, а не расходы и счета.",
    helpAdvancedTitle: "Сложные сценарии", helpAdvancedCopy: "Отдайте инструкцию локальному агенту.",
    helpAdvancedNext: "Вставьте в Claude Code, Codex или Cursor и запустите.",
    helpSkillAgentPrompt: "Загрузи полный каталог skills/sandglass-adapter/ из https://github.com/taiyun668/Sandglass, полностью прочитай его и выполни Sandglass Adapter Skill.",
    helpSkillCopy: "Копировать для агента", helpSkillCopied: "Скопировано", userAdapterShort: "Адаптер проверен", userAdapterVerified: "Проверенный пользовательский адаптер: {sources}",
    customSourceRecords: "Записей подтверждений: {count}", customSourceMirror: "Записей: {count} · известно полей: {recognized} · неизвестно: {unknown}"
  });
  Object.assign(messages["ja-JP"], {
    help: "ヘルプ", back: "戻る", helpTitle: "ヘルプ",
    helpStayLocalTitle: "このPCに留まる", helpStayLocalCopy: "使用量・アカウント・残量は Sandglass 自身のフォルダにだけ書き、アップロードしません。",
    helpReadOnlyTitle: "アカウントはいじらない", helpReadOnlyCopy: "ログイン切替、資格情報の更新、Claude / Codex / Grok のファイル変更はしません。",
    helpCoverageTitle: "証明できる分だけ", helpCoverageCopy: "このPCに公式クライアントが書いた使用量だけを集計し、証明できない分は未割り当て、残量バーには他端末が含まれることがあります。",
    helpBillingTitle: "請求書ではない", helpBillingCopy: "ローカルの活動と公式残量を見るための値で、費用や請求の照合には使えません。",
    helpAdvancedTitle: "複雑なケース", helpAdvancedCopy: "指示をローカル agent に渡す。",
    helpAdvancedNext: "Claude Code、Codex、Cursor に貼り付けて実行する",
    helpSkillAgentPrompt: "https://github.com/taiyun668/Sandglass から skills/sandglass-adapter/ ディレクトリ全体を取得し、すべて読んで Sandglass Adapter Skill を実行してください。",
    helpSkillCopy: "Agent 用にコピー", helpSkillCopied: "コピー済み", userAdapterShort: "ユーザーアダプター検証", userAdapterVerified: "検証済みユーザーアダプター：{sources}",
    customSourceRecords: "証拠 {count} 件", customSourceMirror: "受信 {count} 件・認識 {recognized} 項目・未知 {unknown} 項目"
  });
  Object.assign(messages["ko-KR"], {
    help: "도움말", back: "뒤로", helpTitle: "도움말",
    helpStayLocalTitle: "이 컴퓨터에 남음", helpStayLocalCopy: "사용량, 계정, 할당량은 Sandglass 자체 폴더에만 기록되며 업로드되지 않습니다.",
    helpReadOnlyTitle: "계정을 바꾸지 않음", helpReadOnlyCopy: "로그인을 전환하거나 자격 증명을 새로 고치거나 Claude, Codex, Grok 파일을 수정하지 않습니다.",
    helpCoverageTitle: "증명되는 것만 표시", helpCoverageCopy: "이 컴퓨터에 공식 클라이언트가 기록한 사용량만 집계하고, 증명되지 않은 분은 미할당이며 할당량 막대에는 다른 기기가 포함될 수 있습니다.",
    helpBillingTitle: "청구서가 아님", helpBillingCopy: "로컬 활동과 공식 잔여량을 보기 위한 값이며 비용이나 청구서 대조에 쓰지 마세요.",
    helpAdvancedTitle: "복잡한 상황", helpAdvancedCopy: "로컬 agent에게 지시를 주세요.",
    helpAdvancedNext: "Claude Code, Codex 또는 Cursor에 붙여넣고 실행하세요",
    helpSkillAgentPrompt: "https://github.com/taiyun668/Sandglass 에서 skills/sandglass-adapter/ 전체 디렉터리를 가져와 모두 읽고 Sandglass Adapter Skill을 실행하세요.",
    helpSkillCopy: "Agent용 복사", helpSkillCopied: "복사됨", userAdapterShort: "사용자 어댑터 검증", userAdapterVerified: "검증된 사용자 어댑터: {sources}",
    customSourceRecords: "증거 {count}건", customSourceMirror: "수신 {count}건 · 인식 {recognized}개 · 미인식 {unknown}개"
  });

  Object.assign(messages["zh-CN"], {
    sourceDisplay: "展示", sourceAccounts: "发现账号", sourceAccountsCount: "该来源发现 {count} 个账号，提供 {quotas} 份账号额度；关闭即可从账号列表撤销。", sourceAccount: "关联账号", sourceRecordAccounts: "使用记录内已验证账号", sourceUnmapped: "未关联账号", sourceUnavailable: "当前未发现", sourceHidden: "该来源当前仅保存，不展示证据详情。",
    sourceTotals: "计入总量", sourceFullWindow: "满窗推导",
    sourceStageLocked: "先完成来源规范化与碰撞检查", sourceWindowLocked: "先安全计入总量，并持续标注推导来源",
    sourceNoNormalized: "尚无规范化分钟数据；当前只保存并展示原始证据。",
    sourceShadow: "规范化 {records} 条 · 精确重合 {exact} · 本机缺失 {missing} · 冲突 {conflicts}",
    sourceIdentityApplied: "已用 {count} 个精确重合分钟补充账号归属；没有新增 Token，取消关联即可撤销。",
    sourceTotalsReady: "有 {count} 个无碰撞分钟（{tokens} Token）可由你选择计入。", sourceTotalsApplied: "已计入 {count} 个无碰撞分钟（{tokens} Token）；来源持续标注，关闭即可撤销。", userSourceWindowLocked: "其中含未授权满窗推导的用户适配器证据。", updateTitle: "有新版本 {version}", updateCopy: "下载并校验签名后安装，然后重启。", updateApply: "立即更新", updateApplying: "正在更新…", updateFailed: "更新失败，未做任何改动。", staleQuotaExplanation: "额度读数已过期，暂时无法据此折算满窗。", rolledWindowExplanation: "官方周期已过期，这个时窗按本机记录重新划定，不做满窗折算。", sourceWindowReady: "可单独授权该来源参与满窗推导；推导值旁会持续显示来源。", sourceWindowApplied: "已授权满窗推导，推导值持续标注来源：{sources}。"
  });
  Object.assign(messages["zh-TW"], {
    sourceDisplay: "顯示", sourceAccounts: "發現帳號", sourceAccountsCount: "此來源發現 {count} 個帳號，提供 {quotas} 份帳號額度；關閉即可從帳號清單撤銷。", sourceAccount: "連結帳號", sourceRecordAccounts: "使用記錄內已驗證帳號", sourceUnmapped: "未連結帳號", sourceUnavailable: "目前未發現", sourceHidden: "此來源目前僅保存，不顯示證據詳情。",
    sourceTotals: "計入合計", sourceFullWindow: "完整時窗",
    sourceStageLocked: "請先完成來源正規化與碰撞檢查", sourceWindowLocked: "請先安全計入合計並持續標示推導來源",
    sourceNoNormalized: "尚無正規化分鐘資料；目前只保存並顯示原始證據。",
    sourceShadow: "正規化 {records} 筆 · 精確重合 {exact} · 本機缺少 {missing} · 衝突 {conflicts}",
    sourceIdentityApplied: "已用 {count} 個精確重合分鐘補充帳號歸屬；沒有新增 Token，取消連結即可撤銷。",
    sourceTotalsReady: "有 {count} 個無碰撞分鐘（{tokens} Token）可由你選擇計入。", sourceTotalsApplied: "已計入 {count} 個無碰撞分鐘（{tokens} Token）；來源持續標示，關閉即可撤銷。", userSourceWindowLocked: "其中含未授權完整時窗推導的使用者轉接器證據。", updateTitle: "有新版本 {version}", updateCopy: "下載並驗證簽章後安裝，然後重新啟動。", updateApply: "立即更新", updateApplying: "正在更新…", updateFailed: "更新失敗，未做任何變更。", staleQuotaExplanation: "額度讀數已過期，暫時無法據此推導完整時窗。", rolledWindowExplanation: "官方週期已過期，此時窗依本機紀錄重新劃定，不做完整時窗推導。", sourceWindowReady: "可另行授權此來源參與完整時窗推導；推導值旁會持續顯示來源。", sourceWindowApplied: "已授權完整時窗推導，推導值持續標示來源：{sources}。"
  });
  Object.assign(messages["en-US"], {
    sourceDisplay: "Display", sourceAccounts: "Discovered accounts", sourceAccountsCount: "This source discovered {count} account(s) with {quotas} quota result(s); turn it off to remove them from the account list.", sourceAccount: "Map account", sourceRecordAccounts: "Use verified record identities", sourceUnmapped: "No account mapping", sourceUnavailable: "currently unavailable", sourceHidden: "This source is stored, but its evidence details are hidden.",
    sourceTotals: "Add totals", sourceFullWindow: "Full window",
    sourceStageLocked: "Normalize the source and inspect collisions first", sourceWindowLocked: "Include totals safely first and keep source labels on every estimate",
    sourceNoNormalized: "No normalized minute data yet; only the native evidence is stored and displayed.",
    sourceShadow: "{records} normalized · {exact} exact · {missing} absent locally · {conflicts} conflicts",
    sourceIdentityApplied: "{count} exact minute(s) now supplement account ownership; no Token was added, and unmapping reverses it.",
    sourceTotalsReady: "{count} collision-free minute(s) ({tokens} Token) are ready for your choice.", sourceTotalsApplied: "{count} collision-free minute(s) ({tokens} Token) are included and labeled; turn this off to undo it.", userSourceWindowLocked: "This includes user-adapter evidence not authorized for full-window inference.", updateTitle: "Version {version} is available", updateCopy: "It is downloaded, its signature checked, then installed, and Sandglass restarts.", updateApply: "Update now", updateApplying: "Updating…", updateFailed: "The update did not run. Nothing was changed.", staleQuotaExplanation: "The quota reading has expired, so no full-window estimate is shown.", rolledWindowExplanation: "The official period has expired, so this window was redrawn from local records and no full-window estimate is shown.", sourceWindowReady: "You can separately authorize this source for full-window inference; its name stays beside the estimate.", sourceWindowApplied: "Full-window inference is authorized and keeps these sources beside the estimate: {sources}."
  });
  Object.assign(messages["es-ES"], {
    sourceDisplay: "Mostrar", sourceAccounts: "Cuentas detectadas", sourceAccountsCount: "Esta fuente detectó {count} cuenta(s) y {quotas} resultado(s) de cuota; desactívala para retirarlas de la lista.", sourceAccount: "Vincular cuenta", sourceRecordAccounts: "Usar identidades verificadas del registro", sourceUnmapped: "Sin cuenta vinculada", sourceUnavailable: "no disponible ahora", sourceHidden: "La fuente se conserva, pero sus detalles están ocultos.",
    sourceTotals: "Sumar totales", sourceFullWindow: "Ventana completa",
    sourceStageLocked: "Primero normaliza la fuente y revisa colisiones", sourceWindowLocked: "Primero suma los totales con seguridad y conserva las etiquetas de origen",
    sourceNoNormalized: "Aún no hay minutos normalizados; solo se guarda y muestra la evidencia original.",
    sourceShadow: "{records} normalizados · {exact} exactos · {missing} ausentes localmente · {conflicts} conflictos",
    sourceIdentityApplied: "{count} minuto(s) exactos complementan la atribución de cuenta; no se añadieron Token y desvincular lo revierte.",
    sourceTotalsReady: "Hay {count} minuto(s) sin colisión ({tokens} Token) listos para tu elección.", sourceTotalsApplied: "Se incluyen {count} minuto(s) ({tokens} Token) con su origen; desactívalo para revertir.", userSourceWindowLocked: "Incluye evidencia de adaptador no autorizada para la ventana completa.", updateTitle: "La versión {version} está disponible", updateCopy: "Se descarga, se verifica su firma, se instala y Sandglass se reinicia.", updateApply: "Actualizar ahora", updateApplying: "Actualizando…", updateFailed: "La actualización no se ejecutó. No se cambió nada.", staleQuotaExplanation: "La lectura de cuota ha caducado, por lo que no se estima la ventana completa.", rolledWindowExplanation: "El periodo oficial caducó, así que esta ventana se redefinió con registros locales y no se estima la ventana completa.", sourceWindowReady: "Puedes autorizar esta fuente por separado; su nombre permanecerá junto a la estimación.", sourceWindowApplied: "La ventana completa está autorizada y muestra estas fuentes: {sources}."
  });
  Object.assign(messages["fr-FR"], {
    sourceDisplay: "Afficher", sourceAccounts: "Comptes détectés", sourceAccountsCount: "Cette source a détecté {count} compte(s) et {quotas} résultat(s) de quota ; désactivez-la pour les retirer de la liste.", sourceAccount: "Lier un compte", sourceRecordAccounts: "Utiliser les identités vérifiées des enregistrements", sourceUnmapped: "Aucun compte lié", sourceUnavailable: "indisponible actuellement", sourceHidden: "Cette source est conservée, mais ses détails sont masqués.",
    sourceTotals: "Ajouter aux totaux", sourceFullWindow: "Fenêtre complète",
    sourceStageLocked: "Normalisez d’abord la source et contrôlez les collisions", sourceWindowLocked: "Ajoutez d’abord les totaux sans risque et conservez les sources sur chaque estimation",
    sourceNoNormalized: "Aucune minute normalisée ; seules les preuves natives sont conservées et affichées.",
    sourceShadow: "{records} normalisés · {exact} exacts · {missing} absents localement · {conflicts} conflits",
    sourceIdentityApplied: "{count} minute(s) exacte(s) complètent l’attribution du compte ; aucun Token ajouté, et dissocier annule l’effet.",
    sourceTotalsReady: "{count} minute(s) sans collision ({tokens} Token) attendent votre choix.", sourceTotalsApplied: "{count} minute(s) ({tokens} Token) sont incluses et étiquetées ; désactivez pour annuler.", userSourceWindowLocked: "Ces données incluent un adaptateur non autorisé pour la fenêtre complète.", updateTitle: "La version {version} est disponible", updateCopy: "Elle est téléchargée, sa signature vérifiée, installée, puis Sandglass redémarre.", updateApply: "Mettre à jour", updateApplying: "Mise à jour…", updateFailed: "La mise à jour n’a pas été lancée. Rien n’a été modifié.", staleQuotaExplanation: "La lecture du quota a expiré ; aucune estimation de fenêtre complète n’est affichée.", rolledWindowExplanation: "La période officielle a expiré ; cette fenêtre a été redéfinie à partir des enregistrements locaux, sans estimation de fenêtre complète.", sourceWindowReady: "Vous pouvez autoriser cette source séparément ; son nom restera près de l’estimation.", sourceWindowApplied: "La fenêtre complète est autorisée et affiche ces sources : {sources}."
  });
  Object.assign(messages["de-DE"], {
    sourceDisplay: "Anzeigen", sourceAccounts: "Gefundene Konten", sourceAccountsCount: "Diese Quelle hat {count} Konto/Konten und {quotas} Kontingentwert(e) gefunden; abschalten entfernt sie aus der Kontoliste.", sourceAccount: "Konto zuordnen", sourceRecordAccounts: "Bestätigte Identitäten der Datensätze verwenden", sourceUnmapped: "Kein Konto zugeordnet", sourceUnavailable: "derzeit nicht verfügbar", sourceHidden: "Diese Quelle bleibt gespeichert, ihre Details sind ausgeblendet.",
    sourceTotals: "Zu Summen", sourceFullWindow: "Volles Fenster",
    sourceStageLocked: "Quelle zuerst normalisieren und Kollisionen prüfen", sourceWindowLocked: "Summen zuerst sicher einbeziehen und Quellen an jeder Schätzung zeigen",
    sourceNoNormalized: "Noch keine normalisierten Minuten; native Nachweise werden nur gespeichert und angezeigt.",
    sourceShadow: "{records} normalisiert · {exact} exakt · {missing} lokal fehlend · {conflicts} Konflikte",
    sourceIdentityApplied: "{count} exakt übereinstimmende Minute(n) ergänzen die Kontozuordnung; keine Token wurden addiert, Aufheben macht dies rückgängig.",
    sourceTotalsReady: "{count} kollisionsfreie Minute(n) ({tokens} Token) warten auf deine Auswahl.", sourceTotalsApplied: "{count} Minute(n) ({tokens} Token) sind markiert einbezogen; Abschalten macht dies rückgängig.", userSourceWindowLocked: "Enthält nicht für Vollfenster freigegebene Adapterdaten.", updateTitle: "Version {version} ist verfügbar", updateCopy: "Sie wird geladen, ihre Signatur geprüft, installiert, dann startet Sandglass neu.", updateApply: "Jetzt aktualisieren", updateApplying: "Wird aktualisiert…", updateFailed: "Das Update lief nicht. Es wurde nichts geändert.", staleQuotaExplanation: "Der Kontingentwert ist veraltet, daher wird kein Vollfenster geschätzt.", rolledWindowExplanation: "Der offizielle Zeitraum ist abgelaufen; dieses Fenster wurde aus lokalen Aufzeichnungen neu bestimmt, ohne Vollfenster-Schätzung.", sourceWindowReady: "Diese Quelle kann separat freigegeben werden; ihr Name bleibt neben der Schätzung.", sourceWindowApplied: "Vollfenster ist freigegeben und zeigt diese Quellen: {sources}."
  });
  Object.assign(messages["pt-BR"], {
    sourceDisplay: "Exibir", sourceAccounts: "Contas encontradas", sourceAccountsCount: "Esta fonte encontrou {count} conta(s) e {quotas} resultado(s) de cota; desative para removê-las da lista.", sourceAccount: "Vincular conta", sourceRecordAccounts: "Usar identidades verificadas dos registros", sourceUnmapped: "Sem conta vinculada", sourceUnavailable: "indisponível agora", sourceHidden: "A fonte continua salva, mas os detalhes estão ocultos.",
    sourceTotals: "Somar totais", sourceFullWindow: "Janela completa",
    sourceStageLocked: "Primeiro normalize a fonte e verifique colisões", sourceWindowLocked: "Primeiro inclua totais com segurança e mantenha a origem em cada estimativa",
    sourceNoNormalized: "Ainda não há minutos normalizados; a evidência nativa só é salva e exibida.",
    sourceShadow: "{records} normalizados · {exact} exatos · {missing} ausentes localmente · {conflicts} conflitos",
    sourceIdentityApplied: "{count} minuto(s) exato(s) complementam a atribuição da conta; nenhum Token foi somado e desvincular reverte.",
    sourceTotalsReady: "Há {count} minuto(s) sem colisão ({tokens} Token) aguardando sua escolha.", sourceTotalsApplied: "{count} minuto(s) ({tokens} Token) foram incluídos e rotulados; desative para reverter.", userSourceWindowLocked: "Inclui evidência de adaptador não autorizada para a janela completa.", updateTitle: "A versão {version} está disponível", updateCopy: "Ela é baixada, sua assinatura verificada, instalada, e o Sandglass reinicia.", updateApply: "Atualizar agora", updateApplying: "Atualizando…", updateFailed: "A atualização não foi executada. Nada foi alterado.", staleQuotaExplanation: "A leitura de cota expirou, portanto nenhuma janela completa é estimada.", rolledWindowExplanation: "O período oficial expirou; esta janela foi redefinida a partir dos registros locais, sem estimativa de janela completa.", sourceWindowReady: "Você pode autorizar esta fonte separadamente; o nome ficará junto da estimativa.", sourceWindowApplied: "A janela completa está autorizada e mostra estas fontes: {sources}."
  });
  Object.assign(messages["ru-RU"], {
    sourceDisplay: "Показывать", sourceAccounts: "Найденные аккаунты", sourceAccountsCount: "Источник нашёл аккаунтов: {count}, результатов квоты: {quotas}. Отключите, чтобы убрать их из списка.", sourceAccount: "Связать аккаунт", sourceRecordAccounts: "Использовать проверенные аккаунты записей", sourceUnmapped: "Аккаунт не связан", sourceUnavailable: "сейчас недоступен", sourceHidden: "Источник сохранён, но сведения о нём скрыты.",
    sourceTotals: "Включить в итог", sourceFullWindow: "Полное окно",
    sourceStageLocked: "Сначала нормализуйте источник и проверьте коллизии", sourceWindowLocked: "Сначала безопасно включите итоги и сохраняйте метки источников",
    sourceNoNormalized: "Нормализованных минут пока нет; исходные данные только сохраняются и показываются.",
    sourceShadow: "Нормализовано: {records} · точно: {exact} · нет локально: {missing} · конфликтов: {conflicts}",
    sourceIdentityApplied: "{count} точно совпавших минут дополняют привязку к аккаунту; Token не добавлены, отмена связи всё вернёт.",
    sourceTotalsReady: "Доступно {count} минут без коллизий ({tokens} Token) — выбор за вами.", sourceTotalsApplied: "Включено {count} минут ({tokens} Token) с меткой источника; отключение отменит это.", userSourceWindowLocked: "Есть данные адаптера без разрешения на полное окно.", updateTitle: "Доступна версия {version}", updateCopy: "Она загружается, её подпись проверяется, затем установка и перезапуск.", updateApply: "Обновить", updateApplying: "Обновление…", updateFailed: "Обновление не запущено. Ничего не изменено.", staleQuotaExplanation: "Показание квоты устарело, полное окно не оценивается.", rolledWindowExplanation: "Официальный период истёк; окно пересчитано по локальным записям, без оценки полного окна.", sourceWindowReady: "Источник можно отдельно разрешить; его имя останется рядом с оценкой.", sourceWindowApplied: "Полное окно разрешено и показывает источники: {sources}."
  });
  Object.assign(messages["ja-JP"], {
    sourceDisplay: "表示", sourceAccounts: "検出アカウント", sourceAccountsCount: "このソースは {count} 個のアカウントと {quotas} 件のクォータ結果を検出しました。オフにすると一覧から取り消せます。", sourceAccount: "アカウント対応", sourceRecordAccounts: "記録内の検証済みアカウントを使用", sourceUnmapped: "アカウント未対応", sourceUnavailable: "現在未検出", sourceHidden: "このソースは保存されていますが、証拠の詳細は非表示です。",
    sourceTotals: "合計に追加", sourceFullWindow: "全期間推定",
    sourceStageLocked: "先にソースを正規化して衝突を確認してください", sourceWindowLocked: "先に合計へ安全に追加し、各推定にソースを表示してください",
    sourceNoNormalized: "正規化された分単位データはまだなく、元の証拠だけを保存・表示しています。",
    sourceShadow: "正規化 {records} · 完全一致 {exact} · ローカル欠落 {missing} · 衝突 {conflicts}",
    sourceIdentityApplied: "完全一致した {count} 分をアカウント帰属に使用中です。Token は追加されず、対応解除で元に戻ります。",
    sourceTotalsReady: "衝突のない {count} 分（{tokens} Token）を追加できます。", sourceTotalsApplied: "{count} 分（{tokens} Token）を出典付きで追加中です。オフにすると元に戻ります。", userSourceWindowLocked: "全期間推定を許可していないアダプター証拠を含みます。", updateTitle: "バージョン {version} が利用できます", updateCopy: "ダウンロードして署名を検証し、インストール後に再起動します。", updateApply: "今すぐ更新", updateApplying: "更新中…", updateFailed: "更新は実行されませんでした。変更はありません。", staleQuotaExplanation: "残量の読み取りが古いため、全期間推定は表示しません。", rolledWindowExplanation: "公式の期間が終了しているため、この期間はローカル記録から引き直しました。全期間推定は行いません。", sourceWindowReady: "このソースを別途許可できます。推定値の横に名前を表示し続けます。", sourceWindowApplied: "全期間推定を許可済み。表示するソース：{sources}。"
  });
  Object.assign(messages["ko-KR"], {
    sourceDisplay: "표시", sourceAccounts: "발견된 계정", sourceAccountsCount: "이 소스가 {count}개 계정과 {quotas}개 한도 결과를 찾았습니다. 끄면 계정 목록에서 취소됩니다.", sourceAccount: "계정 연결", sourceRecordAccounts: "레코드의 검증된 계정 사용", sourceUnmapped: "연결된 계정 없음", sourceUnavailable: "현재 찾을 수 없음", sourceHidden: "이 소스는 저장되지만 증거 상세 정보는 숨겨져 있습니다.",
    sourceTotals: "합계에 추가", sourceFullWindow: "전체 구간",
    sourceStageLocked: "먼저 소스를 정규화하고 충돌을 확인하세요", sourceWindowLocked: "먼저 합계에 안전하게 포함하고 모든 추정값에 출처를 표시하세요",
    sourceNoNormalized: "정규화된 분 단위 데이터가 아직 없어 원본 증거만 저장하고 표시합니다.",
    sourceShadow: "정규화 {records} · 정확 일치 {exact} · 로컬 없음 {missing} · 충돌 {conflicts}",
    sourceIdentityApplied: "정확히 일치한 {count}분이 계정 귀속을 보완합니다. Token은 추가되지 않으며 연결 해제로 되돌릴 수 있습니다.",
    sourceTotalsReady: "충돌 없는 {count}분({tokens} Token)을 선택해 합계에 넣을 수 있습니다.", sourceTotalsApplied: "{count}분({tokens} Token)이 출처 표시와 함께 포함되었습니다. 끄면 되돌립니다.", userSourceWindowLocked: "전체 구간 추정을 허용하지 않은 어댑터 증거가 포함됩니다.", updateTitle: "버전 {version} 사용 가능", updateCopy: "내려받아 서명을 검증한 뒤 설치하고 다시 시작합니다.", updateApply: "지금 업데이트", updateApplying: "업데이트 중…", updateFailed: "업데이트가 실행되지 않았습니다. 변경된 것은 없습니다.", staleQuotaExplanation: "할당량 값이 오래되어 전체 구간 추정을 표시하지 않습니다.", rolledWindowExplanation: "공식 구간이 만료되어 이 구간은 로컬 기록으로 다시 잡았습니다. 전체 구간 추정은 하지 않습니다.", sourceWindowReady: "이 소스를 별도로 허용할 수 있으며 이름이 추정값 옆에 계속 표시됩니다.", sourceWindowApplied: "전체 구간 추정을 허용했으며 다음 출처를 표시합니다: {sources}."
  });

  Object.assign(messages["zh-CN"], {
    modeTitle: "你怎样使用 AI 工具？", modeCopy: "选择只影响读取时归属，可随时切换并恢复原账本。",
    modeSingleTitle: "单账号 · 只用官方工具", modeSingleCopy: "把各官方平台监测到的本机用量全部归到该平台当前账号；即使扫描到旧账号也不自动改选。",
    modeSkillTitle: "复杂场景", modeSkillCopy: "切换过账号、使用多个工具，或需要重建、补齐、更新账本时选择。",
    modeChange: "账号与工具模式", modeCancel: "返回，不更改", modeSaveFailed: "使用方式没有保存成功，请重试。",
    unassignedSkill: "这部分本机 Token 缺少可验证的账号关系。请在帮助中使用 Sandglass Adapter Skill 补齐或更新账本；有直接证据的部分仍会正常归属。"
  });
  Object.assign(messages["zh-TW"], {
    modeTitle: "你如何使用 AI 工具？", modeCopy: "選擇只影響讀取時歸屬，可隨時切換並恢復原帳本。",
    modeSingleTitle: "單一帳號 · 僅官方工具", modeSingleCopy: "把各官方平台監測到的本機用量全部歸到該平台目前帳號；即使掃描到舊帳號也不會自動改選。",
    modeSkillTitle: "複雜情境", modeSkillCopy: "切換過帳號、使用多個工具，或需要重建、補齊、更新帳本時選擇。",
    modeChange: "帳號與工具模式", modeCancel: "返回，不變更", modeSaveFailed: "使用方式未儲存成功，請再試一次。",
    unassignedSkill: "這部分本機 Token 缺少可驗證的帳號關係。請在說明中使用 Sandglass Adapter Skill 補齊或更新帳本；有直接證據的部分仍會正常歸屬。"
  });
  Object.assign(messages["en-US"], {
    modeTitle: "How do you use AI tools?", modeCopy: "This changes read-time attribution only; you can switch back without rewriting the ledger.",
    modeSingleTitle: "One account · official tools only", modeSingleCopy: "Assign all local usage observed for each official platform to its current account, even if older accounts are discovered.",
    modeSkillTitle: "Complex scenarios", modeSkillCopy: "Choose this after account switching, when using multiple tools, or to rebuild, complete, or update the ledger.",
    modeChange: "Account and tool mode", modeCancel: "Back without changes", modeSaveFailed: "Your usage mode was not saved. Try again.",
    unassignedSkill: "These local Tokens lack a verified account relationship. Use the Sandglass Adapter Skill in Help to complete or update the ledger; directly evidenced usage remains attributed."
  });
  Object.assign(messages["es-ES"], {
    modeTitle: "¿Cómo usas las herramientas de IA?", modeCopy: "Solo cambia la atribución al leer; puedes volver atrás sin reescribir el registro.",
    modeSingleTitle: "Una cuenta · solo herramientas oficiales", modeSingleCopy: "Atribuye todo el uso local de cada plataforma oficial a su cuenta actual, aunque aparezcan cuentas antiguas.",
    modeSkillTitle: "Escenarios complejos", modeSkillCopy: "Elige esto si cambiaste de cuenta, usas varias herramientas o necesitas completar o actualizar el registro.",
    modeChange: "Modo de cuentas y herramientas", modeCancel: "Volver sin cambios", modeSaveFailed: "No se guardó el modo. Inténtalo de nuevo.",
    unassignedSkill: "Estos Tokens locales no tienen una relación de cuenta verificada. Usa Sandglass Adapter Skill en Ayuda para completar o actualizar el registro; lo probado directamente sigue atribuido."
  });
  Object.assign(messages["fr-FR"], {
    modeTitle: "Comment utilisez-vous les outils IA ?", modeCopy: "Ce choix ne change que l’attribution à la lecture ; le registre reste intact et le retour est possible.",
    modeSingleTitle: "Un compte · outils officiels uniquement", modeSingleCopy: "Attribue tout l’usage local de chaque plateforme officielle à son compte actuel, même si d’anciens comptes sont détectés.",
    modeSkillTitle: "Scénarios complexes", modeSkillCopy: "Choisissez ceci après un changement de compte, avec plusieurs outils, ou pour compléter ou mettre à jour le registre.",
    modeChange: "Mode comptes et outils", modeCancel: "Retour sans modifier", modeSaveFailed: "Le mode n’a pas été enregistré. Réessayez.",
    unassignedSkill: "Ces Tokens locaux n’ont pas de lien de compte vérifié. Utilisez le Sandglass Adapter Skill dans l’aide pour compléter ou mettre à jour le registre ; les usages directement prouvés restent attribués."
  });
  Object.assign(messages["de-DE"], {
    modeTitle: "Wie nutzen Sie KI-Werkzeuge?", modeCopy: "Die Wahl ändert nur die Zuordnung beim Lesen; das Ledger bleibt unverändert und umkehrbar.",
    modeSingleTitle: "Ein Konto · nur offizielle Werkzeuge", modeSingleCopy: "Ordnet die gesamte lokale Nutzung jeder offiziellen Plattform ihrem aktuellen Konto zu, auch wenn alte Konten erkannt werden.",
    modeSkillTitle: "Komplexe Szenarien", modeSkillCopy: "Nach Kontowechseln, bei mehreren Werkzeugen oder zum Neuaufbau, Ergänzen oder Aktualisieren des Ledgers wählen.",
    modeChange: "Konto- und Werkzeugmodus", modeCancel: "Zurück ohne Änderung", modeSaveFailed: "Der Modus wurde nicht gespeichert. Versuchen Sie es erneut.",
    unassignedSkill: "Diesen lokalen Tokens fehlt eine geprüfte Kontobeziehung. Nutzen Sie den Sandglass Adapter Skill in der Hilfe, um das Ledger zu ergänzen oder zu aktualisieren; direkt belegte Nutzung bleibt zugeordnet."
  });
  Object.assign(messages["pt-BR"], {
    modeTitle: "Como você usa ferramentas de IA?", modeCopy: "A escolha só muda a atribuição na leitura; o registro não é regravado e pode ser restaurado.",
    modeSingleTitle: "Uma conta · somente ferramentas oficiais", modeSingleCopy: "Atribui todo o uso local de cada plataforma oficial à conta atual, mesmo que contas antigas sejam encontradas.",
    modeSkillTitle: "Cenários complexos", modeSkillCopy: "Escolha após trocar de conta, ao usar várias ferramentas ou para reconstruir, completar ou atualizar o registro.",
    modeChange: "Modo de contas e ferramentas", modeCancel: "Voltar sem alterar", modeSaveFailed: "O modo não foi salvo. Tente novamente.",
    unassignedSkill: "Estes Tokens locais não têm uma relação de conta verificada. Use a Sandglass Adapter Skill na Ajuda para completar ou atualizar o registro; o uso com prova direta continua atribuído."
  });
  Object.assign(messages["ru-RU"], {
    modeTitle: "Как вы используете ИИ-инструменты?", modeCopy: "Выбор меняет только атрибуцию при чтении; журнал не переписывается и режим можно отменить.",
    modeSingleTitle: "Один аккаунт · только официальные инструменты", modeSingleCopy: "Вся локальная активность каждой официальной платформы относится к её текущему аккаунту, даже если найдены старые аккаунты.",
    modeSkillTitle: "Сложные сценарии", modeSkillCopy: "Выберите после смены аккаунтов, при нескольких инструментах или для восстановления, дополнения либо обновления журнала.",
    modeChange: "Режим аккаунтов и инструментов", modeCancel: "Назад без изменений", modeSaveFailed: "Режим не сохранён. Повторите попытку.",
    unassignedSkill: "У этих локальных Tokens нет проверенной связи с аккаунтом. Используйте Sandglass Adapter Skill в справке, чтобы дополнить или обновить журнал; данные с прямым доказательством остаются атрибутированными."
  });
  Object.assign(messages["ja-JP"], {
    modeTitle: "AI ツールをどのように使いますか？", modeCopy: "選択は読み取り時の帰属だけを変え、台帳を書き換えずいつでも戻せます。",
    modeSingleTitle: "1 アカウント・公式ツールのみ", modeSingleCopy: "古いアカウントが見つかっても、各公式プラットフォームのローカル使用量をすべて現在のアカウントに割り当てます。",
    modeSkillTitle: "複雑なケース", modeSkillCopy: "アカウント切替、複数ツールの利用、台帳の再構築・補完・更新が必要な場合はこちらです。",
    modeChange: "アカウントとツールのモード", modeCancel: "変更せず戻る", modeSaveFailed: "利用形態を保存できませんでした。再試行してください。",
    unassignedSkill: "このローカル Token には検証済みのアカウント関係がありません。ヘルプの Sandglass Adapter Skill で台帳を補完または更新してください。直接証明された利用は引き続き帰属されます。"
  });
  Object.assign(messages["ko-KR"], {
    modeTitle: "AI 도구를 어떻게 사용하나요?", modeCopy: "선택은 읽을 때의 귀속만 바꾸며 원장을 다시 쓰지 않고 언제든 되돌릴 수 있습니다.",
    modeSingleTitle: "계정 1개 · 공식 도구만", modeSingleCopy: "이전 계정이 검색되어도 각 공식 플랫폼의 로컬 사용량을 모두 현재 계정에 귀속합니다.",
    modeSkillTitle: "복잡한 상황", modeSkillCopy: "계정을 전환했거나 여러 도구를 쓰거나 원장을 재구성·보완·업데이트해야 할 때 선택합니다.",
    modeChange: "계정 및 도구 모드", modeCancel: "변경하지 않고 돌아가기", modeSaveFailed: "사용 방식을 저장하지 못했습니다. 다시 시도하세요.",
    unassignedSkill: "이 로컬 Token에는 검증된 계정 관계가 없습니다. 도움말의 Sandglass Adapter Skill로 원장을 보완하거나 업데이트하세요. 직접 증명된 사용량은 계속 귀속됩니다."
  });

  Object.assign(messages["zh-CN"], {
    overviewDisplayMode: "概览显示模式", overviewDefaultMode: "默认模式", overviewCustomMode: "自定义模式",
    activityRangeLabel: "Token 活动范围", rangeAll: "全部", range30d: "30 天", range7d: "7 天", activeDaysSummary: "所选范围内有 {count} 天产生用量",
    addMonitoring: "添加监测", cancelMonitoring: "取消", restoreMonitoring: "添加",
    cancelMonitoringAria: "取消监测 {provider} {account}", restoreMonitoringAria: "重新添加监测 {provider} {account}", noCanceledMonitoring: "暂无已取消监测的账号"
  });
  Object.assign(messages["zh-TW"], {
    overviewDisplayMode: "概覽顯示模式", overviewDefaultMode: "預設模式", overviewCustomMode: "自訂模式",
    activityRangeLabel: "Token 活動範圍", rangeAll: "全部", range30d: "30 天", range7d: "7 天", activeDaysSummary: "所選範圍內有 {count} 天產生用量",
    addMonitoring: "新增監測", cancelMonitoring: "取消", restoreMonitoring: "新增",
    cancelMonitoringAria: "取消監測 {provider} {account}", restoreMonitoringAria: "重新新增監測 {provider} {account}", noCanceledMonitoring: "目前沒有已取消監測的帳號"
  });
  Object.assign(messages["en-US"], {
    overviewDisplayMode: "Overview display mode", overviewDefaultMode: "Default", overviewCustomMode: "Custom",
    activityRangeLabel: "Token activity range", rangeAll: "All", range30d: "30 days", range7d: "7 days", activeDaysSummary: "{count} active days in the selected range",
    addMonitoring: "Add monitoring", cancelMonitoring: "Stop", restoreMonitoring: "Add",
    cancelMonitoringAria: "Stop showing {provider} {account} in monitoring", restoreMonitoringAria: "Add {provider} {account} back to monitoring", noCanceledMonitoring: "No accounts have been removed"
  });
  Object.assign(messages["es-ES"], {
    overviewDisplayMode: "Modo de la vista general", overviewDefaultMode: "Predeterminado", overviewCustomMode: "Personalizado",
    activityRangeLabel: "Intervalo de actividad de tokens", rangeAll: "Todo", range30d: "30 días", range7d: "7 días", activeDaysSummary: "{count} días activos en el intervalo seleccionado",
    addMonitoring: "Añadir seguimiento", cancelMonitoring: "Quitar", restoreMonitoring: "Añadir",
    cancelMonitoringAria: "Quitar {provider} {account} del seguimiento", restoreMonitoringAria: "Volver a añadir {provider} {account} al seguimiento", noCanceledMonitoring: "No hay cuentas quitadas"
  });
  Object.assign(messages["fr-FR"], {
    overviewDisplayMode: "Mode d’affichage", overviewDefaultMode: "Par défaut", overviewCustomMode: "Personnalisé",
    activityRangeLabel: "Période d’activité des tokens", rangeAll: "Tout", range30d: "30 jours", range7d: "7 jours", activeDaysSummary: "{count} jours actifs sur la période sélectionnée",
    addMonitoring: "Ajouter au suivi", cancelMonitoring: "Retirer", restoreMonitoring: "Ajouter",
    cancelMonitoringAria: "Retirer {provider} {account} du suivi", restoreMonitoringAria: "Réajouter {provider} {account} au suivi", noCanceledMonitoring: "Aucun compte retiré"
  });
  Object.assign(messages["de-DE"], {
    overviewDisplayMode: "Übersichtsmodus", overviewDefaultMode: "Standard", overviewCustomMode: "Benutzerdefiniert",
    activityRangeLabel: "Zeitraum der Token-Aktivität", rangeAll: "Alle", range30d: "30 Tage", range7d: "7 Tage", activeDaysSummary: "{count} aktive Tage im ausgewählten Zeitraum",
    addMonitoring: "Überwachung hinzufügen", cancelMonitoring: "Entfernen", restoreMonitoring: "Hinzufügen",
    cancelMonitoringAria: "{provider} {account} aus der Überwachung entfernen", restoreMonitoringAria: "{provider} {account} wieder zur Überwachung hinzufügen", noCanceledMonitoring: "Keine entfernten Konten"
  });
  Object.assign(messages["pt-BR"], {
    overviewDisplayMode: "Modo da visão geral", overviewDefaultMode: "Padrão", overviewCustomMode: "Personalizado",
    activityRangeLabel: "Período de atividade de tokens", rangeAll: "Tudo", range30d: "30 dias", range7d: "7 dias", activeDaysSummary: "{count} dias ativos no período selecionado",
    addMonitoring: "Adicionar monitoramento", cancelMonitoring: "Remover", restoreMonitoring: "Adicionar",
    cancelMonitoringAria: "Remover {provider} {account} do monitoramento", restoreMonitoringAria: "Adicionar {provider} {account} novamente ao monitoramento", noCanceledMonitoring: "Nenhuma conta foi removida"
  });
  Object.assign(messages["ru-RU"], {
    overviewDisplayMode: "Режим обзора", overviewDefaultMode: "По умолчанию", overviewCustomMode: "Свой",
    activityRangeLabel: "Период активности токенов", rangeAll: "Всё", range30d: "30 дней", range7d: "7 дней", activeDaysSummary: "Активных дней в выбранном периоде: {count}",
    addMonitoring: "Добавить наблюдение", cancelMonitoring: "Убрать", restoreMonitoring: "Добавить",
    cancelMonitoringAria: "Убрать {provider} {account} из наблюдения", restoreMonitoringAria: "Вернуть {provider} {account} в наблюдение", noCanceledMonitoring: "Удалённых аккаунтов нет"
  });
  Object.assign(messages["ja-JP"], {
    overviewDisplayMode: "概要の表示モード", overviewDefaultMode: "デフォルト", overviewCustomMode: "カスタム",
    activityRangeLabel: "Token アクティビティの期間", rangeAll: "すべて", range30d: "30日", range7d: "7日", activeDaysSummary: "選択期間の記録日数は {count} 日",
    addMonitoring: "監視に追加", cancelMonitoring: "解除", restoreMonitoring: "追加",
    cancelMonitoringAria: "{provider} {account} の監視を解除", restoreMonitoringAria: "{provider} {account} を監視に再追加", noCanceledMonitoring: "監視を解除したアカウントはありません"
  });
  Object.assign(messages["ko-KR"], {
    overviewDisplayMode: "개요 표시 모드", overviewDefaultMode: "기본", overviewCustomMode: "사용자 지정",
    activityRangeLabel: "Token 활동 기간", rangeAll: "전체", range30d: "30일", range7d: "7일", activeDaysSummary: "선택한 기간의 활동 일수 {count}일",
    addMonitoring: "모니터링 추가", cancelMonitoring: "취소", restoreMonitoring: "추가",
    cancelMonitoringAria: "{provider} {account} 모니터링 취소", restoreMonitoringAria: "{provider} {account} 모니터링 다시 추가", noCanceledMonitoring: "모니터링을 취소한 계정이 없습니다"
  });

  Object.assign(messages["zh-CN"], { languageOptions: "语言选项" });
  Object.assign(messages["zh-TW"], { languageOptions: "語言選項" });
  Object.assign(messages["en-US"], { languageOptions: "Language options" });
  Object.assign(messages["es-ES"], { languageOptions: "Opciones de idioma" });
  Object.assign(messages["fr-FR"], { languageOptions: "Options de langue" });
  Object.assign(messages["de-DE"], { languageOptions: "Sprachoptionen" });
  Object.assign(messages["pt-BR"], { languageOptions: "Opções de idioma" });
  Object.assign(messages["ru-RU"], { languageOptions: "Параметры языка" });
  Object.assign(messages["ja-JP"], { languageOptions: "言語オプション" });
  Object.assign(messages["ko-KR"], { languageOptions: "언어 옵션" });

  Object.assign(messages["zh-CN"], { localUsageLoading: "正在整理本机用量…", localUsageRetrying: "正在重新整理本机用量…", localUsageUnavailable: "本机用量暂未更新", retry: "重试" });
  Object.assign(messages["zh-TW"], { localUsageLoading: "正在整理本機用量…", localUsageRetrying: "正在重新整理本機用量…", localUsageUnavailable: "本機用量暫未更新", retry: "重試" });
  Object.assign(messages["en-US"], { localUsageLoading: "Preparing local usage…", localUsageRetrying: "Retrying local usage…", localUsageUnavailable: "Local usage has not updated", retry: "Retry" });
  Object.assign(messages["es-ES"], { localUsageLoading: "Preparando el uso local…", localUsageRetrying: "Reintentando el uso local…", localUsageUnavailable: "El uso local no se ha actualizado", retry: "Reintentar" });
  Object.assign(messages["fr-FR"], { localUsageLoading: "Préparation de l’usage local…", localUsageRetrying: "Nouvelle tentative pour l’usage local…", localUsageUnavailable: "L’usage local n’a pas été actualisé", retry: "Réessayer" });
  Object.assign(messages["de-DE"], { localUsageLoading: "Lokale Nutzung wird aufbereitet…", localUsageRetrying: "Lokale Nutzung wird erneut geladen…", localUsageUnavailable: "Lokale Nutzung wurde nicht aktualisiert", retry: "Erneut versuchen" });
  Object.assign(messages["pt-BR"], { localUsageLoading: "Preparando o uso local…", localUsageRetrying: "Tentando carregar o uso local novamente…", localUsageUnavailable: "O uso local não foi atualizado", retry: "Tentar novamente" });
  Object.assign(messages["ru-RU"], { localUsageLoading: "Подготовка локального расхода…", localUsageRetrying: "Повторное получение локального расхода…", localUsageUnavailable: "Локальный расход не обновлён", retry: "Повторить" });
  Object.assign(messages["ja-JP"], { localUsageLoading: "ローカル使用量を集計中…", localUsageRetrying: "ローカル使用量を再取得中…", localUsageUnavailable: "ローカル使用量が更新されていません", retry: "再試行" });
  Object.assign(messages["ko-KR"], { localUsageLoading: "로컬 사용량 정리 중…", localUsageRetrying: "로컬 사용량 다시 불러오는 중…", localUsageUnavailable: "로컬 사용량이 업데이트되지 않음", retry: "다시 시도" });

  const updateFlowTranslations = {
    "zh-CN": { updateTooltip: "安装更新并重启（{version}）", updateConfirmTitle: "安装更新并重启？", updateConfirmDescription: "将安装版本 {version}，应用会随后重启。", updateCancel: "取消", updateInstallRestart: "安装并重启", announcementTitle: "更新公告", announcementDismiss: "知道了", announcementClose: "关闭更新公告" },
    "zh-TW": { updateTooltip: "安裝更新並重新啟動（{version}）", updateConfirmTitle: "安裝更新並重新啟動？", updateConfirmDescription: "將安裝版本 {version}，應用程式隨後會重新啟動。", updateCancel: "取消", updateInstallRestart: "安裝並重新啟動", announcementTitle: "更新公告", announcementDismiss: "知道了", announcementClose: "關閉更新公告" },
    "en-US": { updateTooltip: "Install update and restart ({version})", updateConfirmTitle: "Install update and restart?", updateConfirmDescription: "Version {version} will be installed, then Sandglass will restart.", updateCancel: "Cancel", updateInstallRestart: "Install and restart", announcementTitle: "Release notes", announcementDismiss: "Got it", announcementClose: "Close release notes" },
    "es-ES": { updateTooltip: "Instalar actualización y reiniciar ({version})", updateConfirmTitle: "¿Instalar la actualización y reiniciar?", updateConfirmDescription: "Se instalará la versión {version} y Sandglass se reiniciará.", updateCancel: "Cancelar", updateInstallRestart: "Instalar y reiniciar", announcementTitle: "Novedades", announcementDismiss: "Entendido", announcementClose: "Cerrar novedades" },
    "fr-FR": { updateTooltip: "Installer la mise à jour et redémarrer ({version})", updateConfirmTitle: "Installer la mise à jour et redémarrer ?", updateConfirmDescription: "La version {version} sera installée, puis Sandglass redémarrera.", updateCancel: "Annuler", updateInstallRestart: "Installer et redémarrer", announcementTitle: "Nouveautés", announcementDismiss: "Compris", announcementClose: "Fermer les nouveautés" },
    "de-DE": { updateTooltip: "Update installieren und neu starten ({version})", updateConfirmTitle: "Update installieren und neu starten?", updateConfirmDescription: "Version {version} wird installiert; danach startet Sandglass neu.", updateCancel: "Abbrechen", updateInstallRestart: "Installieren und neu starten", announcementTitle: "Änderungen", announcementDismiss: "Verstanden", announcementClose: "Änderungen schließen" },
    "pt-BR": { updateTooltip: "Instalar atualização e reiniciar ({version})", updateConfirmTitle: "Instalar atualização e reiniciar?", updateConfirmDescription: "A versão {version} será instalada e o Sandglass será reiniciado.", updateCancel: "Cancelar", updateInstallRestart: "Instalar e reiniciar", announcementTitle: "Novidades", announcementDismiss: "Entendi", announcementClose: "Fechar novidades" },
    "ru-RU": { updateTooltip: "Установить обновление и перезапустить ({version})", updateConfirmTitle: "Установить обновление и перезапустить?", updateConfirmDescription: "Будет установлена версия {version}, затем Sandglass перезапустится.", updateCancel: "Отмена", updateInstallRestart: "Установить и перезапустить", announcementTitle: "Что нового", announcementDismiss: "Понятно", announcementClose: "Закрыть описание" },
    "ja-JP": { updateTooltip: "アップデートをインストールして再起動（{version}）", updateConfirmTitle: "アップデートをインストールして再起動しますか？", updateConfirmDescription: "バージョン {version} をインストールした後、Sandglass を再起動します。", updateCancel: "キャンセル", updateInstallRestart: "インストールして再起動", announcementTitle: "更新のお知らせ", announcementDismiss: "了解", announcementClose: "更新のお知らせを閉じる" },
    "ko-KR": { updateTooltip: "업데이트 설치 후 다시 시작({version})", updateConfirmTitle: "업데이트를 설치하고 다시 시작할까요?", updateConfirmDescription: "버전 {version}을 설치한 뒤 Sandglass를 다시 시작합니다.", updateCancel: "취소", updateInstallRestart: "설치 후 다시 시작", announcementTitle: "업데이트 안내", announcementDismiss: "알겠습니다", announcementClose: "업데이트 안내 닫기" },
  };
  for (const [locale, additions] of Object.entries(updateFlowTranslations)) Object.assign(messages[locale], additions);

  const options = [
    { id: "zh-CN", label: "简体中文" },
    { id: "zh-TW", label: "繁體中文" },
    { id: "en-US", label: "English" },
    { id: "es-ES", label: "Español" },
    { id: "fr-FR", label: "Français" },
    { id: "de-DE", label: "Deutsch" },
    { id: "pt-BR", label: "Português (Brasil)" },
    { id: "ru-RU", label: "Русский" },
    { id: "ja-JP", label: "日本語" },
    { id: "ko-KR", label: "한국어" }
  ];

  const requiredKeys = Object.keys(messages["zh-CN"]);
  for (const option of options) {
    const missing = requiredKeys.filter(key => !Object.prototype.hasOwnProperty.call(messages[option.id], key));
    if (missing.length) throw new Error(`Missing ${option.id} translations: ${missing.join(", ")}`);
  }

  function resolve(value) {
    const lang = String(value || "").toLowerCase();
    if (lang.startsWith("zh") && /(?:^|[-_])(tw|hk|mo|hant)(?:$|[-_])/.test(lang)) return "zh-TW";
    if (lang.startsWith("zh")) return "zh-CN";
    if (lang.startsWith("es")) return "es-ES";
    if (lang.startsWith("fr")) return "fr-FR";
    if (lang.startsWith("de")) return "de-DE";
    if (lang.startsWith("pt")) return "pt-BR";
    if (lang.startsWith("ru")) return "ru-RU";
    if (lang.startsWith("ja")) return "ja-JP";
    if (lang.startsWith("ko")) return "ko-KR";
    return "en-US";
  }

  function translate(locale, key, values) {
    const source = messages[locale] || messages["en-US"];
    const template = source[key] || messages["zh-CN"][key] || key;
    return String(template).replace(/\{(\w+)\}/g, (_, name) =>
      values && values[name] != null ? String(values[name]) : ""
    );
  }

  window.SANDGLASS_I18N = Object.freeze({ messages, options, resolve, translate });
}());
