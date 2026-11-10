// Gjør en openGym-økt om til Garmin-format med openGyms egen .fit-eksport (lib/fit-export.js).
// Leser {workout, customEx, unit} på stdin, skriver {fit: base64, sets: [...]} på stdout.
// `sets` er i samme form som Garmins exerciseSets, klare for set_activity_exercise_sets.
import { readFileSync } from 'node:fs'
const LIB = (process.env.OPENGYM_LIB || '/opengym-src/lib').replace(/\/$/, '')
const { workoutFit, workoutFitMessages, FIT_EPOCH_OFFSET, MSG } = await import(LIB + '/fit-export.js')
const { registerCustom } = await import(LIB + '/exercises.js')

const CATEGORIES = ['BENCH_PRESS', 'CALF_RAISE', 'CARDIO', 'CARRY', 'CHOP', 'CORE', 'CRUNCH', 'CURL', 'DEADLIFT',
  'FLYE', 'HIP_RAISE', 'HIP_STABILITY', 'HIP_SWING', 'HYPEREXTENSION', 'LATERAL_RAISE', 'LEG_CURL', 'LEG_RAISE',
  'LUNGE', 'OLYMPIC_LIFT', 'PLANK', 'PLYO', 'PULL_UP', 'PUSH_UP', 'ROW', 'SHOULDER_PRESS', 'SHOULDER_STABILITY',
  'SHRUG', 'SIT_UP', 'SQUAT', 'TOTAL_BODY', 'TRICEPS_EXTENSION', 'WARM_UP', 'RUN']

const { workout, customEx = [], unit = 'kg' } = JSON.parse(readFileSync(0, 'utf8'))
registerCustom(customEx)
const field = (m, n) => (m.fields.find(f => f[0] === n) || [])[2]
const iso = fitSec => new Date((fitSec + FIT_EPOCH_OFFSET) * 1000).toISOString().replace('Z', '').replace(/\.\d+$/, '.0')
const sets = workoutFitMessages(workout, { unit }).filter(m => m.global === MSG.set).map((m, i) => {
  const active = field(m, 5) === 1
  const cat = field(m, 7)
  const weight16 = field(m, 4)
  return {
    exercises: active ? [{ category: CATEGORIES[cat] || 'UNKNOWN', name: null, probability: 100 }] : [],
    duration: Math.round((field(m, 0) || 0) / 1000),
    repetitionCount: active ? field(m, 3) ?? null : null,
    weight: active && weight16 != null ? Math.round(weight16 / 16 * 1000) : null,
    setType: active ? 'ACTIVE' : 'REST',
    startTime: iso(field(m, 6)),
    wktStepIndex: null,
    messageIndex: i,
  }
})
const fit = Buffer.from(workoutFit(workout, { unit })).toString('base64')
process.stdout.write(JSON.stringify({ fit, sets }))
