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
    error.value = friendly(e.message || 'Something went wrong')
    return null
  } finally {
    busy.value = false
  }
}

// The wall tablet leaves the network when it sleeps, and the raw failure
// ("cannot reach the tablet at 192.168.1.115") reads as the app being broken.
// Name the actual cause and the actual fix instead.
const ASLEEP =
  'The wall tablet is not responding - it is probably asleep. ' +
  'Wake its screen and this will reconnect on its own.'

function friendly(message) {
  if (/cannot reach|not responding|timed out|Failed to fetch|NetworkError/i.test(message)) {
    return ASLEEP
  }
  return message
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
    error.value = friendly(e.message || 'Cannot reach the heating system')
  }
}

const post = (path, body) =>
  call(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
    .then((r) => { if (r) status.value = r })

const heatOn = (temp) => post('/api/heat-on', { temp })

// A preset has to do both: make sure the system is on and heating, then set
// the target on the room that actually governs it.
async function quickSet(temp) {
  await heatOn(temp)
  if (controlling.value) await setZoneTemp(controlling.value.id, temp)
}
const turnOff = () => post('/api/power/off')
const setTemp = (temp) => post('/api/temp', { temp })
const setZone = (id, change) => post(`/api/zone/${id}`, change)
const setZoneTemp = (id, temp) => post(`/api/zone/${id}/temp`, { temp })

// The unit follows one zone (myZone); its target is what actually drives the
// heating, so that is the number the UI has to put front and centre.
const controlling = computed(() =>
  status.value?.zones?.find((z) => z.isControlling) ?? null,
)
const roomTarget = computed(() => controlling.value?.setTemp ?? target.value)
const roomUp = () =>
  controlling.value &&
  setZoneTemp(controlling.value.id, Math.min(roomTarget.value + step.value, version.value.maxTemp ?? 30))
const roomDown = () =>
  controlling.value &&
  setZoneTemp(controlling.value.id, Math.max(roomTarget.value - step.value, version.value.minTemp ?? 16))

const isOn = computed(() => status.value?.state === 'on')
const isHeating = computed(() => isOn.value && status.value?.mode === 'heat')
const target = computed(() => status.value?.setTemp ?? 20)

// The step comes from the backend: this system only accepts whole degrees,
// and a half-degree target is silently ignored by the tablet.
const step = computed(() => version.value.tempStep ?? 1)
const stepUp = () => setTemp(Math.min(target.value + step.value, version.value.maxTemp ?? 30))
const stepDown = () => setTemp(Math.max(target.value - step.value, version.value.minTemp ?? 16))

const fmt = (t) => {
  if (t === null || t === undefined) return '--'
  const n = Number(t)
  // Whole-degree systems should not show a pointless '.0'
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}

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
        <span v-if="isHeating">{{ fmt(roomTarget) }}&deg;</span>
        <span v-else-if="isOn">On</span>
        <span v-else>Off</span>
      </div>
      <div class="sub">
        <span v-if="isHeating && controlling">
          {{ controlling.name }} &middot; now {{ fmt(controlling.measuredTemp) }}&deg;,
          heating to {{ fmt(roomTarget) }}&deg;
        </span>
        <span v-else-if="isHeating">Heating to {{ fmt(target) }}&deg;</span>
        <span v-else-if="isOn">Running in {{ status?.mode }} mode</span>
        <span v-else>The heating is off</span>
      </div>
    </div>

    <div class="msg-slot">
      <div v-if="error" class="msg err" role="alert">{{ error }}</div>
      <div v-else-if="busy" class="msg busy" role="status">Working&hellip;</div>
    </div>

    <div class="row">
      <button
        class="btn-heat"
        :class="{ active: isHeating }"
        :disabled="busy"
        @click="quickSet(roomTarget)"
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
      <h2 v-if="controlling">Temperature in {{ controlling.name }}</h2>
      <h2 v-else>Temperature</h2>
      <div class="row" style="align-items: center">
        <button
          class="btn-step"
          :disabled="busy"
          @click="controlling ? roomDown() : stepDown()"
          :aria-label="`Cooler by ${step} degree`"
        >&minus;</button>
        <div class="temp-display">{{ fmt(roomTarget) }}&deg;</div>
        <button
          class="btn-step"
          :disabled="busy"
          @click="controlling ? roomUp() : stepUp()"
          :aria-label="`Warmer by ${step} degree`"
        >+</button>
      </div>
    </div>

    <div class="card">
      <h2>Quick set</h2>
      <div class="row">
        <button
          v-for="t in PRESETS"
          :key="t"
          :class="{ active: isHeating && Number(roomTarget) === t }"
          :disabled="busy"
          @click="quickSet(t)"
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
          <span v-if="z.isControlling" class="tag">controls heating</span>
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
