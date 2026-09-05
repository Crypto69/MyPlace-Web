<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const status = ref(null)
const error = ref('')
const busy = ref(false)
const version = ref({})

// One-tap presets. Chosen so the common cases never need the +/- buttons.
const PRESETS = [18, 20, 22]

let poll = null

async function call(path, opts = {}) {
  busy.value = true
  error.value = ''
  try {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(body.detail || `Error ${res.status}`)
    return body
  } catch (e) {
    error.value = e.message || 'Something went wrong'
    return null
  } finally {
    busy.value = false
  }
}

async function refresh() {
  // Background polls must not flip the busy flag, or the buttons would
  // flicker to disabled every few seconds.
  try {
    const res = await fetch('/api/status')
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(body.detail || `Error ${res.status}`)
    status.value = body
    error.value = ''
  } catch (e) {
    error.value = e.message || 'Cannot reach the heating system'
  }
}

const post = (path, body) =>
  call(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
    .then((r) => { if (r) status.value = r })

const heatOn = (temp) => post('/api/heat-on', { temp })
const turnOff = () => post('/api/power/off')
const setTemp = (temp) => post('/api/temp', { temp })
const setZone = (id, change) => post(`/api/zone/${id}`, change)

const isOn = computed(() => status.value?.state === 'on')
const isHeating = computed(() => isOn.value && status.value?.mode === 'heat')
const target = computed(() => status.value?.setTemp ?? 20)

const stepUp = () => setTemp(Math.min(target.value + 0.5, version.value.maxTemp ?? 30))
const stepDown = () => setTemp(Math.max(target.value - 0.5, version.value.minTemp ?? 16))

const fmt = (t) => (t === null || t === undefined ? '--' : Number(t).toFixed(1))

onMounted(async () => {
  version.value = (await call('/api/version')) || {}
  await refresh()
  poll = setInterval(refresh, 10000)
})
onUnmounted(() => clearInterval(poll))
</script>

<template>
  <div class="wrap">
    <div class="card status">
      <h1>{{ status?.name || 'Heating' }}</h1>
      <div class="big">
        <span class="dot" :class="{ on: isOn }"></span>
        <span v-if="isHeating">{{ fmt(target) }}&deg;</span>
        <span v-else-if="isOn">On</span>
        <span v-else>Off</span>
      </div>
      <div class="sub">
        <span v-if="isHeating">Heating to {{ fmt(target) }}&deg;</span>
        <span v-else-if="isOn">Running in {{ status?.mode }} mode</span>
        <span v-else>The heating is off</span>
      </div>
    </div>

    <div v-if="error" class="msg err" role="alert">{{ error }}</div>
    <div v-else-if="busy" class="msg busy" role="status">Working&hellip;</div>

    <div class="row">
      <button
        class="btn-heat"
        :class="{ active: isHeating }"
        :disabled="busy"
        @click="heatOn(target)"
      >
        Heat On
      </button>
      <button
        class="btn-off"
        :class="{ active: !isOn }"
        :disabled="busy"
        @click="turnOff"
      >
        Turn Off
      </button>
    </div>

    <div class="card">
      <h2>Temperature</h2>
      <div class="row" style="align-items: center">
        <button class="btn-step" :disabled="busy" @click="stepDown" aria-label="Warmer by half a degree">&minus;</button>
        <div class="temp-display">{{ fmt(target) }}&deg;</div>
        <button class="btn-step" :disabled="busy" @click="stepUp" aria-label="Cooler by half a degree">+</button>
      </div>
    </div>

    <div class="card">
      <h2>Quick set</h2>
      <div class="row">
        <button
          v-for="t in PRESETS"
          :key="t"
          :class="{ active: isHeating && Number(target) === t }"
          :disabled="busy"
          @click="heatOn(t)"
        >
          {{ t }}&deg;
        </button>
      </div>
    </div>

    <div class="card" v-if="status?.zones?.length">
      <h2>Rooms</h2>
      <div class="zone" v-for="z in status.zones" :key="z.id">
        <div class="name">
          {{ z.name }}
          <div class="meta">
            <span v-if="z.measuredTemp !== null && z.measuredTemp !== undefined">
              now {{ fmt(z.measuredTemp) }}&deg;
            </span>
            <span v-if="z.hasSensor && z.setTemp"> &middot; set {{ fmt(z.setTemp) }}&deg;</span>
            <span v-else-if="!z.hasSensor && z.value !== null"> &middot; {{ z.value }}% open</span>
          </div>
        </div>
        <button
          :class="z.state === 'open' ? 'btn-open' : 'btn-close'"
          :disabled="busy"
          @click="setZone(z.id, { state: z.state === 'open' ? 'close' : 'open' })"
        >
          {{ z.state === 'open' ? 'On' : 'Off' }}
        </button>
      </div>
    </div>

    <div class="foot">
      build {{ version.sha }} &middot; tablet {{ version.tablet || 'not set' }}
    </div>
  </div>
</template>
