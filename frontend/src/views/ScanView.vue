<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { showToast, showConfirmDialog, showSuccessToast, showFailToast } from 'vant'
import { Html5Qrcode } from 'html5-qrcode'
import QRCode from 'qrcode'
import { useBathhouseStore } from '@/stores/bathhouse'
import { useUserStore } from '@/stores/user'
import {
  pushReport,
  bumpFrequent,
  saveActiveSession,
  clearActiveSession,
  loadActiveSession,
} from '@/utils/bathHistory'
import FaultReportPopup from '@/components/FaultReportPopup.vue'
import type { Bathhouse, FaultType, Stall } from '@/types'

/**
 * 扫码使用页（按「入场/离场扫码」想法实现）：
 * 1) 入场：扫隔间二维码（码内容 STALL|{楼id}|{隔间号}）→ 确认 → 隔间变「使用中」并开始计时；
 * 2) 离场：再次扫码（或点「结束使用」）→ 询问是否有故障 → 无故障恢复空闲；
 *    有故障则打开报修弹窗（预选该隔间），提交后隔间标为「维修」；
 * 3) 完成后自动生成「洗澡报告」：洗澡时长 + 平均水温 + 一句习惯分析；
 * 4) 摄像头默认使用后置（对外），无后置或不可用时回退手动输入模拟扫码。
 */
const store = useBathhouseStore()
const userStore = useUserStore()
const router = useRouter()

/** 当前浴室（单楼模式） */
const current = (): Bathhouse | undefined => store.bathhouses[0]

/** 隔间状态文字与颜色：空闲/使用中/维修 三态（后端 cleaning 归入使用中） */
function stateOf(s: Stall) {
  if (s.status === 'idle') return { l: '空闲', cls: 'st-idle' }
  if (s.status === 'fault') return { l: '维修', cls: 'st-fault' }
  return { l: '使用中', cls: 'st-busy' }
}

/* ============ 页面状态机：idle（待扫码）→ using（使用中，结束即回到 idle） ============ */

const mode = ref<'idle' | 'using'>('idle')

/** 本次使用会话：隔间、浴室、开始时间、基准水温 */
const session = ref<{ stallId: string; bathId: number; startAt: number; waterBase: number } | null>(null)

/** 已使用秒数（计时器每秒更新） */
const elapsed = ref(0)
let timer: ReturnType<typeof setInterval> | null = null

/** 已使用时长文案 mm:ss */
const elapsedText = computed(() => {
  const m = Math.floor(elapsed.value / 60)
  const s = elapsed.value % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
})

/** 当前展示水温（基准水温 ± 轻微波动，模拟传感器读数） */
const currentWater = computed(() => {
  const base = session.value?.waterBase ?? 41
  return +(base + Math.sin(Date.now() / 3000) * 0.4).toFixed(1)
})

/**
 * 传感器自动离场（核心：无需用户手动结束/再扫码）：
 * 模拟 门磁+水流 传感器——入场后持续监测水流，检测到「水流停止超过阈值」即判定人已离开，
 * 自动结束使用、恢复隔间空闲并生成洗澡报告。同学洗完就走，系统自己知道。
 */
// 演示值：入场 5 分钟后模拟水流停止 → 判定离场（真实系统由硬件实时判定）
// 至少 5 分钟：避免同学洗澡中途切出扫码页（看消息/切应用）被误判离场；想提前出报告可用「立即结束」
const SENSOR_AUTO_EXIT_MS = 300000
let sensorTimer: ReturnType<typeof setTimeout> | null = null
/** 模拟「水流停止」发生的时刻（用于面板展示传感器状态） */
const sensorStopAt = ref(0)

/** 传感器水流状态：flowing=水流中（在用） / stopped=已停止（判定离场中）。依赖 elapsed 每秒重算 */
const sensorFlow = computed(() => {
  void elapsed.value // 每秒 tick 触发重算，保证到点后状态翻转
  if (!session.value) return 'flowing'
  return Date.now() >= sensorStopAt.value ? 'stopped' : 'flowing'
})

/**
 * 开始传感器监测：入场时启动，到点判定水流停止并自动结束使用。
 * 剩余时间基于入场时间戳计算，这样离开页面再回来恢复时也能正确衔接。
 */
function startSensorMonitor() {
  stopSensorMonitor()
  const base = session.value?.startAt ?? Date.now()
  sensorStopAt.value = base + SENSOR_AUTO_EXIT_MS
  // 剩余监测时间 = 停止时刻 - 当前时刻（恢复场景可能只剩几秒）
  const remain = Math.max(0, sensorStopAt.value - Date.now())
  sensorTimer = setTimeout(() => {
    if (!session.value) return
    showToast('传感器检测到水流停止，判定已离场')
    finishUsage(false, true) // 自动离场（无故障）
  }, remain)
}
function stopSensorMonitor() {
  if (sensorTimer) {
    clearTimeout(sensorTimer)
    sensorTimer = null
  }
}

/** 开始计时 */
function startTimer() {
  stopTimer()
  timer = setInterval(() => elapsed.value++, 1000)
}
function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

/* ============ 摄像头扫码（后置优先） ============ */

const scanning = ref(false)
const camError = ref('')
let html5Qr: Html5Qrcode | null = null
/** 防止扫码回调重复触发（同一码连续识别） */
let handleOnce = true

/** 枚举到的摄像头列表（用于优先后置 + 手动切换） */
const cams = ref<{ id: string; label: string }[]>([])
let camIdx = 0

/** 枚举可用摄像头（含前置/后置） */
async function enumerateCams() {
  try {
    cams.value = await Html5Qrcode.getCameras()
  } catch {
    cams.value = []
  }
}

/**
 * 启动扫码：优先后置摄像头（对外扫码，label 含 back/rear/environment 视为后置）；
 * 找不到后置则回退 facingMode environment；多摄像头时可手动切换。
 */
async function startScanner() {
  if (scanning.value) return
  if (!cams.value.length) await enumerateCams()
  // 后置优先：后置通常排在列表靠后，且 label 含 back/rear/environment 字样
  const back =
    cams.value.find((c) => /back|rear|environment/i.test(c.label || '')) ??
    cams.value[cams.value.length - 1]
  const config: string | { facingMode: string } = back ? back.id : { facingMode: 'environment' }
  await startWith(config)
}

/** 用指定摄像头/模式启动扫码，失败（无权限/非 HTTPS）时提示手动输入兜底 */
async function startWith(config: string | { facingMode: string }) {
  try {
    html5Qr = new Html5Qrcode('scan-reader')
    await html5Qr.start(
      config,
      { fps: 10, qrbox: { width: 240, height: 240 } },
      (text) => onScanned(text),
      () => {}
    )
    scanning.value = true
    camError.value = ''
  } catch (e) {
    camError.value = '无法打开摄像头（需 HTTPS 或 localhost 环境），请使用下方「手动输入」'
    scanning.value = false
  }
}

/** 停止并释放摄像头（识别成功后调用，避免取景框常驻） */
async function stopScanner() {
  if (html5Qr && scanning.value) {
    try {
      await html5Qr.stop()
      html5Qr.clear()
    } catch {
      /* 摄像头已被外部释放时忽略 */
    }
    scanning.value = false
  }
}

/** 摄像头识别回调：只处理第一次识别，然后停流并解析 */
function onScanned(text: string) {
  if (!handleOnce) return
  handleOnce = false
  stopScanner()
  resolveCode(text)
}

/** 手动切换前置/后置摄像头（多摄像头时按钮可见） */
async function switchCamera() {
  if (cams.value.length < 2) {
    showToast('当前设备只有一个摄像头')
    return
  }
  handleOnce = true
  await stopScanner()
  camIdx = (camIdx + 1) % cams.value.length
  await startWith(cams.value[camIdx].id)
  showToast(camIdx === 0 ? '已切换到前置摄像头' : '已切换到后置摄像头')
}

/** 重新扫码：重置一次性开关并重启摄像头 */
async function rescan() {
  handleOnce = true
  result.value = null
  startScanner()
}

/* ============ 码解析与定位 ============ */

/** 当前已定位的隔间（扫码/手动输入/点演示码后设置） */
const result = ref<Stall | null>(null)

/** 手动输入的隔间码 */
const manualCode = ref('')

/**
 * 解析扫码文本 → 隔间编码。
 * 支持完整格式 STALL|1|F01 或用户直接输入 F01；大小写不敏感。
 */
function parseStallId(raw: string): string | null {
  const t = raw.trim().toUpperCase()
  const full = t.match(/^STALL\|\d+\|([A-Z]\d+)$/)
  const plain = t.match(/^([A-Z]\d+)$/)
  return full?.[1] ?? plain?.[1] ?? null
}

/** 根据隔间码定位隔间并展示结果卡片 */
function resolveCode(raw: string) {
  const stallId = parseStallId(raw)
  if (!stallId) {
    showToast('未识别到有效隔间码，请确认格式如 F01')
    return
  }
  const b = current()
  const s = b?.stalls.find((x) => x.stallId === stallId)
  if (!s) {
    showToast(`未找到隔间 ${stallId}`)
    return
  }
  result.value = s
  showToast(`已定位隔间 ${stallId}`)
}

/** 结果卡片当前隔间是否为本机正在使用的隔间（离场判断） */
const isMyUsing = computed(() => result.value?.stallId === session.value?.stallId)

/* ============ 入场使用 ============ */

/** 入场：确认后标记隔间使用中 + 开始计时（传感器语义：门磁闭合） */
async function onEnter() {
  const s = result.value
  if (!s) return
  // reserved 是「已预约未到场」，允许扫码核销；是否属于本人由后端校验
  if (s.status !== 'idle' && s.status !== 'reserved') {
    showToast(stateOf(s).l + '中，不可入场')
    return
  }
  try {
    await showConfirmDialog({
      title: '入场确认',
      message: `确认使用隔间 ${s.stallId} 吗？开始后将为您计时`,
      confirmButtonText: '开始使用',
      cancelButtonText: '再看看',
    })
  } catch {
    return // 用户取消
  }
  const b = current()!
  try {
    // 先让后端核销（reserved → occupied，并校验票号归属），失败则不入场
    await store.startUseAt(b.id, s.stallId)
  } catch (e) {
    showFailToast((e as Error).message || '核销失败，请重新取号')
    return
  }
  session.value = { stallId: s.stallId, bathId: b.id, startAt: Date.now(), waterBase: b.waterTemp }
  // 扫码使用的浴室计入「常去浴室」（按隔间号累计，供「我的」页统计）
  if (userStore.current?.account) {
    bumpFrequent(userStore.current.account, s.stallId, b.name)
    // 使用中会话持久化：离开扫码页再回来，计时与传感器状态不丢
    saveActiveSession(userStore.current.account, {
      stallId: s.stallId,
      bathId: b.id,
      waterBase: b.waterTemp,
      startAt: session.value.startAt,
    })
  }
  elapsed.value = 0
  startTimer()
  startSensorMonitor() // 启动传感器监测：水流停止后自动判定离场
  mode.value = 'using'
  showSuccessToast(`已入场 ${s.stallId}，传感器正在为您计时`)
  refreshResult(s.stallId)
}

/* ============ 离场：手动兜底（传感器自动判定为主，此入口仅备用） ============ */

/** 离场确认：询问是否有故障；有故障则先走报修弹窗，无故障直接完成 */
async function onExit() {
  if (!session.value) return
  // 最短时长门槛：不足 2 分钟不允许提前结束，避免"30 秒洗完"这类荒谬报告
  //（传感器仍判定水流中，真正洗完等自动离场即可）
  if (elapsed.value < 120) {
    showToast('洗澡时间过短（不足 2 分钟），传感器仍在监测中，请稍候')
    return
  }
  stopSensorMonitor() // 用户手动结束，取消自动判定，避免双重触发
  let hasFault = false
  try {
    await showConfirmDialog({
      title: '离场确认',
      message: '本次使用是否遇到故障问题？',
      confirmButtonText: '没有故障',
      cancelButtonText: '有故障，去报修',
    })
  } catch {
    hasFault = true
  }
  if (hasFault) {
    // 打开报修弹窗（预选当前隔间），提交后由 onReported 走完成流程
    showReport.value = true
    return
  }
  await finishUsage(false)
}

/** 报修弹窗提交：先上报并标记维修，再完成流程 */
async function onReported(stallId: number, type: FaultType, detail: string) {
  try {
    // 必须 await：submitFault 内部先上报（含 mock 延迟）再改隔间状态
    await store.submitFault(current()!.id, stallId, type, detail)
    await finishUsage(true)
  } catch (e) {
    showFailToast((e as Error).message || '报修提交失败')
  }
}

/**
 * 完成使用：恢复隔间状态（无故障→空闲；有故障→已由报修标记为维修）、
 * 停止计时并将洗澡报告持久化到「我的」页（扫码页不展示报告，结束即回到待扫码状态）。
 * viaSensor=true 表示由传感器自动判定离场（人已走，无需操作）。
 */
async function finishUsage(hasFault: boolean, viaSensor = false) {
  const s = session.value
  if (!s) return
  stopTimer()
  stopSensorMonitor()
  // 无故障：通知后端释放隔间；有故障：submitFault 已在后端标记维修并解除使用绑定
  if (!hasFault) {
    try {
      await store.endUseAt(s.bathId, s.stallId)
    } catch (e) {
      // 同步失败不阻塞本地结束流程，但提示用户后端可能仍是占用状态
      showFailToast((e as Error).message || '离场同步失败')
    }
  }
  // 生成洗澡报告数据：真实计时时长 + 模拟平均水温 + 习惯分析文案，仅持久化到「我的」页
  const minutes = elapsed.value / 60
  const water = +(s.waterBase + (Math.random() * 2 - 1)).toFixed(1)
  const tip = habitTip(minutes, water)
  const acct = userStore.current?.account
  if (acct) {
    const b = store.bathhouses.find((x) => x.id === s.bathId)
    pushReport({
      account: acct,
      bathId: s.bathId,
      bathName: b?.name ?? '',
      stallId: s.stallId,
      minutes,
      water,
      tip,
      at: Date.now(),
    })
    // 结束使用后清除未结束的会话（下次入场重新扫码）
    clearActiveSession(acct)
  }
  session.value = null
  result.value = null
  // 结束即回到待扫码状态（报告不在此页展示，去「我的」页查看）
  mode.value = 'idle'
  handleOnce = true
  startScanner()
  showSuccessToast(viaSensor ? '已检测到离场，报告已生成' : '使用完成，报告已生成')
}

/** 习惯分析文案：按时长与水温给出一句个性化总结（对接「分析用户习惯」想法） */
function habitTip(minutes: number, water: number): string {
  // 最短使用门槛（2 分钟）已拦截超短时长，此处只需覆盖正常洗浴区间
  if (minutes < 10) return '清爽派：时间控制得当，不浪费一分一秒'
  if (water < 39) return '偏好偏凉水温，洗后精神一整天'
  if (water > 42) return '偏爱热水包裹感，舒适解乏的最佳选择'
  return '温和水温最合你心意，享受刚刚好'
}

/** 按隔间编码刷新结果卡片（状态联动后取最新） */
function refreshResult(stallId: string) {
  const fresh = current()?.stalls.find((x) => x.stallId === stallId)
  if (fresh) result.value = { ...fresh }
}

/**
 * 恢复历史状态（进入页面时调用）：
 * 若存在未结束的使用中会话 → 恢复计时面板（按入场时间戳推算已用时长），
 * 传感器若已判定水流停止（超过阈值）→ 立即自动离场生成报告（仅写入「我的」页历史）；
 * 无会话 → 回到全新的待扫码状态。
 * @returns 是否已恢复（true 时不再启动摄像头扫码）
 */
function restoreState(): boolean {
  const acct = userStore.current?.account
  if (!acct) return false
  const active = loadActiveSession(acct)
  if (active) {
    const b = store.bathhouses.find((x) => x.id === active.bathId)
    const stall = b?.stalls.find((x) => x.stallId === active.stallId)
    if (stall) {
      // 恢复使用中：计时从入场时刻推算，传感器按剩余时间续测
      session.value = {
        stallId: active.stallId,
        bathId: active.bathId,
        startAt: active.startAt,
        waterBase: active.waterBase,
      }
      elapsed.value = Math.max(0, Math.floor((Date.now() - active.startAt) / 1000))
      // 传感器判定已过阈值 → 人其实已经洗完离开，直接自动离场（报告写入「我的」页）
      if (Date.now() - active.startAt >= SENSOR_AUTO_EXIT_MS) {
        showToast('传感器检测到水流停止，判定已离场')
        finishUsage(false, true)
        return true
      }
      mode.value = 'using'
      startTimer()
      startSensorMonitor()
      return true
    }
    // 会话指向的隔间已不存在（数据重置），清理脏数据
    clearActiveSession(acct)
  }
  return false
}

/* ============ 演示二维码区 ============ */

/** 每个隔间生成二维码 dataURL（码内容：STALL|{楼id}|{隔间号}） */
const qrMap = ref<Record<string, string>>({})

async function buildQrs() {
  const b = current()
  if (!b) return
  const map: Record<string, string> = {}
  for (const s of b.stalls) {
    map[s.stallId] = await QRCode.toDataURL(`STALL|${b.id}|${s.stallId}`, {
      width: 56,
      margin: 1,
    })
  }
  qrMap.value = map
}

/* ============ 报修弹窗 ============ */

const showReport = ref(false)

/** 报修弹窗预选隔间：优先当前定位的隔间，其次正在使用的隔间（离场报修场景） */
const reportPresetId = computed(() => {
  if (result.value) return result.value.id
  if (session.value) {
    const s = current()?.stalls.find((x) => x.stallId === session.value!.stallId)
    return s?.id ?? null
  }
  return null
})

/* ============ 生命周期 ============ */

onMounted(async () => {
  // 确保浴室数据就绪（扫码页可能被直接进入，未经过首页 fetch）
  if (!store.bathhouses.length) await store.fetchBathhouses()
  await buildQrs()
  // 优先恢复未结束的使用中会话 / 最近报告；只有全新状态才自动启动摄像头
  if (!restoreState()) startScanner()
})

onBeforeUnmount(() => {
  stopTimer()
  stopSensorMonitor()
  stopScanner()
})

/** 结果卡片状态样式 */
const resultCls = computed(() => (result.value ? stateOf(result.value).cls : ''))
</script>

<template>
  <div class="page">
    <!-- 顶部标题 -->
    <div class="topbar">
      <div class="hello">扫码使用</div>
      <div class="sub">
        {{ mode === 'using' ? '传感器监测中 · 洗完离开后自动结束并生成报告' : '扫隔间二维码 · 入场使用（离场全自动）' }}
      </div>
    </div>

    <div v-if="!store.bathhouses.length" class="loading">正在加载浴室数据…</div>

    <template v-else>
      <!-- ========== 使用中面板 ========== -->
      <div v-if="mode === 'using' && session" class="card using-card">
        <div class="using-head">
          <span class="dot"></span>
          <span>正在使用中</span>
          <span class="u-stall">{{ session.stallId }}</span>
        </div>
        <div class="using-main">
          <div class="u-timer">{{ elapsedText }}</div>
          <div class="u-sub">已使用时长 · {{ current()!.name }}</div>
        </div>
        <div class="using-row">
          <div class="u-cell">
            <b>{{ currentWater.toFixed(1) }}℃</b>
            <span>当前水温</span>
          </div>
          <div class="u-cell">
            <b>{{ session.waterBase }}℃</b>
            <span>基准水温</span>
          </div>
          <div class="u-cell">
            <b :class="sensorFlow === 'stopped' ? 'off' : 'on'">
              {{ sensorFlow === 'stopped' ? '已停止' : '水流中' }}
            </b>
            <span>水流传感器</span>
          </div>
        </div>
        <button class="exit-btn" @click="onExit">
          <van-icon name="passed" /> 立即结束（备用）
        </button>
      </div>

      <template v-else>
        <!-- ========== 摄像头扫码区 ========== -->
        <div class="card scan-card">
          <div class="sec-title">
            <span><van-icon name="scan" /> 摄像头扫码</span>
            <span v-if="scanning" class="tip">后置对准隔间码 · 识别后自动停止</span>
          </div>
          <!-- html5-qrcode 渲染取景框；摄像头不可用时改为友好占位，避免大黑框 -->
          <div v-if="!camError" id="scan-reader" class="reader" />
          <div v-else class="cam-off">
            <van-icon name="scan" class="cam-off-icon" />
            <div class="cam-off-title">当前环境无法调用摄像头</div>
            <div class="cam-off-sub">请在手机浏览器上体验真实扫码；电脑上可直接用下方「手动输入」，或点底部演示二维码直接定位</div>
          </div>
          <div class="scan-btns">
            <button v-if="scanning" class="ghost-btn" @click="stopScanner">停止扫码</button>
            <!-- 多摄像头时提供前置/后置切换（扫码默认后置对外） -->
            <button v-if="cams.length > 1" class="ghost-btn" @click="switchCamera">
              <van-icon name="photograph" /> 切换摄像头
            </button>
          </div>
        </div>

        <!-- ========== 手动输入兜底（模拟扫码） ========== -->
        <div class="card" :class="{ 'manual-card': camError }">
          <div class="sec-title">
            <span>
              <van-icon v-if="camError" name="guide-o" style="color: var(--free)" />
              手动输入隔间码
            </span>
            <span v-if="camError" class="badge-recommend">当前推荐</span>
            <span v-else class="tip">摄像头不可用时使用</span>
          </div>
          <div class="manual-row">
            <input
              v-model="manualCode"
              class="manual-input"
              placeholder="输入隔间码，如 F01"
              @keyup.enter="resolveCode(manualCode)"
            />
            <button class="primary-btn" @click="resolveCode(manualCode)">模拟扫码</button>
          </div>
        </div>

        <!-- ========== 识别结果卡片 ========== -->
        <div v-if="result" class="card result-card">
          <div class="sec-title">
            <span>扫码结果</span>
            <button class="ghost-btn small" @click="rescan">
              <van-icon name="replay" /> 重新扫码
            </button>
          </div>
          <div class="result-main">
            <div class="result-stall" :class="resultCls">
              <b>{{ result.stallId }}</b>
              <span>{{ stateOf(result).l }}</span>
            </div>
            <div class="result-info">
              <div class="ri-name">{{ current()!.name }}</div>
              <div class="ri-sub">{{ result.zone }} · 隔间 {{ result.stallId }}</div>
            </div>
          </div>
          <!-- 按状态给操作：空闲→入场使用；本机使用中→结束使用；他人使用中/维修→仅报修 -->
          <div class="result-actions">
            <template v-if="result.status === 'idle'">
              <button class="reserve-btn" @click="onEnter">
                <van-icon name="passed" /> 入场使用
              </button>
              <button class="repair-btn" @click="showReport = true">
                <van-icon name="warning-o" /> 快速报修
              </button>
            </template>
            <template v-else-if="isMyUsing">
              <button class="reserve-btn" @click="onExit">
                <van-icon name="passed" /> 结束使用（离场）
              </button>
            </template>
            <template v-else>
              <button class="repair-btn" :disabled="result.status === 'fault'" @click="showReport = true">
                <van-icon name="warning-o" />
                {{ result.status === 'fault' ? '维修中' : '快速报修' }}
              </button>
            </template>
          </div>
        </div>
      </template>

      <!-- ========== 演示二维码区 ========== -->
      <div class="card">
        <div class="sec-title">
          <span>演示二维码</span>
          <span class="tip">用另一台设备扫码 · 或直接点格子</span>
        </div>
        <div class="qr-grid">
          <div
            v-for="s in current()!.stalls"
            :key="s.stallId"
            class="qr-cell"
            @click="resolveCode(qrMap[s.stallId] ? `STALL|${current()!.id}|${s.stallId}` : s.stallId)"
          >
            <img v-if="qrMap[s.stallId]" :src="qrMap[s.stallId]" :alt="`${s.stallId} 二维码`" />
            <div class="qr-no" :class="stateOf(s).cls">{{ s.stallId }}</div>
          </div>
        </div>
        <div class="legend">码内容格式：STALL|{{ current()!.id }}|FXX；手机摄像头扫下方码即可自动定位</div>
      </div>

      <!-- 报修弹窗（预设当前定位/正在使用的隔间） -->
      <FaultReportPopup
        v-model:show="showReport"
        :bath-name="current()!.name"
        :zones="current()!.zones"
        :preset-stall-id="reportPresetId"
        @reported="onReported"
      />
    </template>
  </div>
</template>

<style scoped>
.page {
  padding: 18px 16px 30px;
  width: 100%;
  margin: 0 auto;
}
.topbar {
  margin-bottom: 14px;
}
.hello {
  font-size: 28px;
  font-weight: 800;
  letter-spacing: 0.5px;
}
.sub {
  font-size: 14px;
  color: var(--text-sub);
  margin-top: 4px;
}
.loading {
  text-align: center;
  color: var(--text-sub);
  padding: 80px 0;
}

/* 通用卡片 */
.card {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px;
  margin-bottom: 12px;
}
.sec-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 12px;
}
.tip {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-sub);
}
.legend {
  font-size: 11px;
  color: var(--text-sub);
  margin-top: 10px;
  text-align: center;
}

/* 摄像头取景框 */
.scan-card {
  text-align: center;
}
.reader {
  width: 100%;
  max-width: 320px;
  margin: 0 auto;
  border-radius: 12px;
  overflow: hidden;
  background: #0b0f14;
  min-height: 180px;
}
.reader :deep(video) {
  width: 100%;
  display: block;
}
/* 摄像头不可用时的友好占位卡（替代黑色空框 + 红色报错） */
.cam-off {
  max-width: 320px;
  margin: 0 auto;
  min-height: 180px;
  border-radius: 12px;
  background: var(--bg-card-2);
  border: 1px dashed var(--line);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 20px;
  text-align: center;
}
.cam-off-icon {
  font-size: 40px;
  color: var(--text-sub);
  opacity: 0.7;
}
.cam-off-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-main);
}
.cam-off-sub {
  font-size: 12px;
  color: var(--text-sub);
  line-height: 1.6;
}
.scan-btns {
  display: flex;
  justify-content: center;
  gap: 10px;
}

/* 摄像头不可用时手动输入卡高亮为当前主入口 */
.manual-card {
  border-color: rgba(7, 163, 90, 0.45);
  background: var(--brand-dim);
}
.badge-recommend {
  font-size: 11px;
  font-weight: 600;
  color: #0b0f14;
  background: var(--free);
  border-radius: 999px;
  padding: 2px 10px;
}

/* 手动输入 */
.manual-row {
  display: flex;
  gap: 10px;
}
.manual-input {
  flex: 1;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--bg-card-2);
  padding: 11px 12px;
  font-size: 15px;
  color: var(--text-main);
}
.manual-input::placeholder {
  color: var(--text-sub);
}
.primary-btn {
  border: none;
  border-radius: 10px;
  background: var(--free);
  color: #0b0f14;
  font-weight: 700;
  font-size: 14px;
  padding: 0 18px;
  cursor: pointer;
}
.ghost-btn {
  border: 1px solid var(--line);
  background: var(--bg-card-2);
  color: var(--text-sub);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 12px;
  margin-top: 10px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.ghost-btn.small {
  margin-top: 0;
}

/* 扫码结果卡片 */
.result-card {
  border-color: rgba(7, 163, 90, 0.35);
}
.result-main {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 6px 2px 12px;
}
.result-stall {
  width: 74px;
  padding: 12px 0;
  border-radius: 12px;
  text-align: center;
  border: 1px solid var(--line);
  background: var(--bg-card-2);
}
.result-stall b {
  display: block;
  font-size: 20px;
  font-weight: 800;
}
.result-stall span {
  font-size: 11px;
}
.st-idle { color: var(--free); border-color: rgba(15, 220, 120, 0.35); }
.st-busy { color: var(--busy); border-color: rgba(255, 152, 0, 0.35); }
.st-fault { color: var(--full); border-color: rgba(232, 70, 58, 0.35); }
.ri-name {
  font-size: 15px;
  font-weight: 700;
}
.ri-sub {
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 3px;
}

/* 结果操作按钮 */
.result-actions {
  display: flex;
  gap: 10px;
}
.reserve-btn {
  flex: 1;
  border: none;
  border-radius: 10px;
  background: linear-gradient(135deg, #0fdc78, #05994f);
  color: #0b0f14;
  font-size: 15px;
  font-weight: 700;
  padding: 12px 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
}
.reserve-btn.full {
  width: 100%;
  margin-top: 10px;
}
.reserve-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.repair-btn {
  flex: 1;
  border: 1px solid var(--busy);
  border-radius: 10px;
  background: transparent;
  color: var(--busy);
  font-size: 15px;
  font-weight: 600;
  padding: 12px 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
}
.repair-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* 使用中面板 */
.using-card {
  border-color: rgba(7, 163, 90, 0.4);
}
.using-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
}
.using-head .dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--free);
  animation: blink 1.2s infinite;
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.u-stall {
  margin-left: auto;
  color: var(--free);
  font-weight: 800;
}
.using-main {
  text-align: center;
  padding: 18px 0 10px;
}
.u-timer {
  font-size: 44px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  letter-spacing: 1px;
  color: var(--text-main);
}
.u-sub {
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 4px;
}
.using-row {
  display: flex;
  gap: 10px;
  margin: 6px 0 16px;
}
.u-cell {
  flex: 1;
  background: var(--bg-card-2);
  border: 1px solid var(--line);
  border-radius: 10px;
  text-align: center;
  padding: 10px 0;
}
.u-cell b {
  display: block;
  font-size: 16px;
  font-weight: 700;
  color: var(--free);
}
.u-cell b.on {
  color: var(--free);
}
.u-cell b.off {
  color: var(--busy);
}
.u-cell span {
  font-size: 10px;
  color: var(--text-sub);
}
.exit-btn {
  width: 100%;
  border: none;
  border-radius: 10px;
  background: linear-gradient(135deg, #0fdc78, #05994f);
  color: #0b0f14;
  font-size: 16px;
  font-weight: 700;
  padding: 13px 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
}

/* 演示二维码网格：8 列超紧凑，进一步缩小二维码卡片尺寸 */
.qr-grid {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 6px;
}
.qr-cell {
  text-align: center;
  background: var(--bg-card-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 4px 2px;
  cursor: pointer;
}
.qr-cell:active {
  opacity: 0.7;
}
.qr-cell img {
  width: 100%;
  height: auto;
  display: block;
  border-radius: 4px;
}
.qr-no {
  font-size: 9px;
  font-weight: 700;
  margin-top: 3px;
}
</style>
