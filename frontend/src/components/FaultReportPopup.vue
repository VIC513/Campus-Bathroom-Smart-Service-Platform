<script setup lang="ts">
import { ref, computed } from 'vue'
import { showSuccessToast } from 'vant'
import { FAULT_TYPES, FAULT_REASON_MAP } from '@/types'
import type { FaultType, Stall, Bathzone } from '@/types'

const props = defineProps<{
  show: boolean
  /** 当前楼名 */
  bathName: string
  /** 该楼的全部分区隔间 */
  zones: Bathzone[]
}>()

const emit = defineEmits<{
  (e: 'update:show', v: boolean): void
  /** 报修成功：上抛隔间 id、故障类型、自定义描述 */
  (e: 'reported', stallId: number, type: FaultType, detail: string): void
}>()

const selectedId = ref<number | null>(null)
const faultType = ref<FaultType | null>(null)
// 自定义问题描述
const detail = ref('')

/** 全部分区隔间（扁平化） */
const allStalls = computed(() => props.zones.flatMap((z) => z.stalls))
/** 可选隔间：故障(维修中)的隔间不可再报修 */
const selectable = computed(() => allStalls.value.filter((s) => s.status !== 'fault'))

/** 选「其他（自定义）」时需填写描述 */
const needDetail = computed(() => faultType.value === 'other')

function onPick(stallId: number) {
  selectedId.value = stallId
}

function onSubmit() {
  if (!faultType.value || selectedId.value == null) return
  if (faultType.value === 'other' && !detail.value.trim()) return
  emit('reported', selectedId.value, faultType.value, detail.value.trim())
  const stall = allStalls.value.find((s) => s.id === selectedId.value)
  const reason = FAULT_REASON_MAP[faultType.value]
  showSuccessToast(`已提交报修：${props.bathName} · ${stall?.stallId ?? selectedId.value} · ${reason}`)
  reset()
}

function onClose() {
  reset()
  emit('update:show', false)
}

function reset() {
  selectedId.value = null
  faultType.value = null
  detail.value = ''
}
</script>

<template>
  <van-popup :show="show" position="bottom" round class="rp" @update:show="(v) => emit('update:show', v)">
    <div class="rp-head">设备故障报修</div>

    <!-- 步骤 1：选择故障隔间（按分区分组） -->
    <div class="rp-label">选择故障隔间</div>
    <div v-for="z in props.zones" :key="z.name" class="rp-zone">
      <span class="rp-zone-name">{{ z.name }}</span>
      <div class="rp-chips">
        <button
          v-for="s in z.stalls"
          :key="s.id"
          class="rp-chip"
          :class="{ active: selectedId === s.id, disabled: s.status === 'fault' }"
          :disabled="s.status === 'fault'"
          @click="onPick(s.id)"
        >
          {{ s.stallId }}
        </button>
      </div>
    </div>

    <!-- 步骤 2：选择故障类型 -->
    <div class="rp-label">故障类型</div>
    <div class="rp-types">
      <button
        v-for="t in FAULT_TYPES"
        :key="t.value"
        class="rp-type"
        :class="{ active: faultType === t.value }"
        @click="faultType = t.value"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- 步骤 3：自定义描述（选「其他（自定义）」时出现） -->
    <div v-if="needDetail" class="rp-detail">
      <div class="rp-label">请描述具体问题</div>
      <textarea
        v-model="detail"
        class="rp-ta"
        rows="3"
        maxlength="100"
        placeholder="例如：花洒水流过小 / 隔间门把手松动…"
      />
    </div>

    <div class="rp-note">
      <van-icon name="warning-o" />
      <span>提交后该隔间将标记为「维修」，他人不可再选</span>
    </div>

    <div class="rp-actions">
      <button class="rp-cancel" @click="onClose">取消</button>
      <button
        class="rp-submit"
        :disabled="!(faultType && selectedId != null) || (needDetail && !detail.trim())"
        @click="onSubmit"
      >
        提交报修
      </button>
    </div>
  </van-popup>
</template>

<style scoped>
.rp {
  padding: 18px 16px 26px;
  max-height: 82vh;
  overflow-y: auto;
  background: var(--bg-card);
  color: var(--text-main);
}
.rp-head {
  font-size: 17px;
  font-weight: 700;
  margin-bottom: 14px;
}
.rp-label {
  font-size: 13px;
  color: var(--text-sub);
  margin: 12px 0 8px;
}
.rp-zone {
  margin-bottom: 10px;
}
.rp-zone-name {
  font-size: 12px;
  color: var(--text-sub);
  margin-right: 8px;
}
.rp-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.rp-chip {
  min-width: 46px;
  border: 1px solid var(--line);
  background: var(--bg-card-2);
  color: var(--text-main);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 13px;
  cursor: pointer;
}
.rp-chip.active {
  border-color: var(--free);
  color: var(--free);
  background: rgba(15, 220, 120, 0.12);
}
.rp-chip.disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.rp-types {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}
.rp-type {
  border: 1px solid var(--line);
  background: var(--bg-card-2);
  color: var(--text-main);
  border-radius: 10px;
  padding: 10px 0;
  font-size: 14px;
  cursor: pointer;
}
.rp-type.active {
  border-color: var(--free);
  color: var(--free);
  background: rgba(15, 220, 120, 0.12);
}
.rp-ta {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--bg-card-2);
  color: var(--text-main);
  font-size: 14px;
  padding: 10px;
  resize: none;
}
.rp-ta::placeholder {
  color: var(--text-sub);
}
.rp-note {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 12px;
}
.rp-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}
.rp-cancel {
  flex: 1;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--text-sub);
  font-size: 15px;
  padding: 11px 0;
  border-radius: 999px;
  cursor: pointer;
}
.rp-submit {
  flex: 2;
  border: none;
  background: var(--free);
  color: #0b0f14;
  font-weight: 700;
  font-size: 15px;
  padding: 11px 0;
  border-radius: 999px;
  cursor: pointer;
}
.rp-submit:disabled {
  opacity: 0.5;
}
</style>
