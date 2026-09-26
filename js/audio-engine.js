// Web Audio API engine: file -> source -> filters -> effects -> analyser -> speakers
// Graph: AudioElement -> MediaElementSource -> Bass(low shelf) -> Treble(high shelf)
//       -> Distortion(waveshaper) -> Delay(echo) -> Convolver(reverb-lite via delay) -> Stereo(panner)
//       -> Gain(master) -> Analyser -> destination
// A second gain chain mixes synthesized funny sounds + chaos.
class AudioEngine {
  constructor(audioEl) {
    this.audio = audioEl;
    this.ctx = null; this.nodes = {}; this.fxGain = null;
    this.chaosTimer = null; this.chaosLevel = "OFF";
  }
  ensure() {
    if (this.ctx) { if (this.ctx.state === "suspended") this.ctx.resume(); return; }
    const AC = window.AudioContext || window.webkitAudioContext;
    this.ctx = new AC();
    const c = this.ctx;
    const src = c.createMediaElementSource(this.audio);
    const bass = c.createBiquadFilter(); bass.type = "lowshelf"; bass.frequency.value = 200;
    const treble = c.createBiquadFilter(); treble.type = "highshelf"; treble.frequency.value = 4000;
    const shaper = c.createWaveShaper(); shaper.curve = this._flatCurve(); shaper.oversample = "4x";
    const echo = c.createDelay(1); echo.delayTime.value = 0.25;
    const echoGain = c.createGain(); echoGain.gain.value = 0;
    const echoFb = c.createGain(); echoFb.gain.value = 0.35;
    const reverb = c.createDelay(1); reverb.delayTime.value = 0.03;
    const reverbGain = c.createGain(); reverbGain.gain.value = 0;
    const panner = c.createStereoPanner ? c.createStereoPanner() : c.createGain();
    const master = c.createGain(); master.gain.value = this.audio.volume;
    const analyser = c.createAnalyser(); analyser.fftSize = 256;

    src.connect(bass); bass.connect(treble); treble.connect(shaper);
    shaper.connect(master);
    // echo send
    shaper.connect(echo); echo.connect(echoFb); echoFb.connect(echo); echo.connect(echoGain); echoGain.connect(master);
    // cheap reverb send
    shaper.connect(reverb); reverb.connect(reverbGain); reverbGain.connect(master);
    master.connect(panner);
    // funny-sound bus
    this.fxGain = c.createGain(); this.fxGain.gain.value = 0.9; this.fxGain.connect(panner);
    panner.connect(analyser); analyser.connect(c.destination);

    this.nodes = { bass, treble, shaper, echo, echoGain, reverbGain, panner, master, analyser };
  }
  _flatCurve() { const n = 256, curve = new Float32Array(n); for (let i = 0; i < n; i++) curve[i] = (i / (n - 1)) * 2 - 1; return curve; }
  _distCurve(k) { const n = 256, c = new Float32Array(n); for (let i = 0; i < n; i++) { const x = (i / (n - 1)) * 2 - 1; c[i] = Math.tanh(k * x); } return c; }

  set(patch) {
    this.ensure();
    const N = this.nodes, a = this.audio;
    if (patch.volume !== undefined) { a.volume = patch.volume; N.master.gain.value = patch.volume; }
    if (patch.bass !== undefined) N.bass.gain.value = (patch.bass - 50) / 50 * 15;
    if (patch.treble !== undefined) N.treble.gain.value = (patch.treble - 50) / 50 * 15;
    if (patch.speed !== undefined) a.playbackRate.value = patch.speed;
    if (patch.pitch !== undefined && a.preservesPitch !== undefined) a.preservesPitch = patch.pitch >= 50;
    if (patch.echo !== undefined) N.echoGain.gain.value = patch.echo / 100 * 0.6;
    if (patch.reverb !== undefined) N.reverbGain.gain.value = patch.reverb / 100 * 0.5;
    if (patch.distortion !== undefined) N.shaper.curve = patch.distortion <= 2 ? this._flatCurve() : this._distCurve(1 + patch.distortion / 12);
    if (patch.stereo !== undefined && N.panner.pan) N.panner.pan.value = (patch.stereo - 50) / 50;
    if (patch.vocal !== undefined) N.treble.frequency.value = 2500 + patch.vocal * 30; // vocal presence trick
  }
  preset(name) {
    const P = {
      Normal: { bass: 50, treble: 50, echo: 0, reverb: 0, distortion: 0, stereo: 50 },
      "Deep Bass": { bass: 95, treble: 35, echo: 0, reverb: 10, distortion: 0, stereo: 50 },
      Club: { bass: 80, treble: 70, echo: 15, reverb: 25, distortion: 0, stereo: 50 },
      Rock: { bass: 70, treble: 85, echo: 0, reverb: 15, distortion: 25, stereo: 50 },
      Chill: { bass: 55, treble: 45, echo: 25, reverb: 35, distortion: 0, stereo: 50 },
      Robot: { bass: 60, treble: 90, echo: 40, reverb: 10, distortion: 45, stereo: 50 },
      Underwater: { bass: 85, treble: 10, echo: 30, reverb: 50, distortion: 5, stereo: 50 },
      "Old Radio": { bass: 20, treble: 80, echo: 0, reverb: 5, distortion: 30, stereo: 50 },
      "8-Bit": { bass: 50, treble: 95, echo: 0, reverb: 0, distortion: 60, stereo: 50 },
      Funny: { bass: 75, treble: 75, echo: 35, reverb: 20, distortion: 20, stereo: 20 },
      Chaos: { bass: 90, treble: 90, echo: 50, reverb: 40, distortion: 55, stereo: 50 },
    };
    return P[name] || P.Normal;
  }
  // ---- synthesized funny sounds (no files needed) ----
  async playSynth(kind) {
    this.ensure();
    const c = this.ctx, t = c.currentTime, g = c.createGain();
    g.gain.value = 0.8; g.connect(this.fxGain);
    const osc = (type, f0, f1, dur) => {
      const o = c.createOscillator(); o.type = type;
      o.frequency.setValueAtTime(f0, t);
      o.frequency.exponentialRampToValueAtTime(Math.max(f1, 1), t + dur);
      o.connect(g); o.start(t); o.stop(t + dur + 0.05);
    };
    const noise = (dur) => {
      const b = c.createBuffer(1, c.sampleRate * dur, c.sampleRate);
      const d = b.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
      const s = c.createBufferSource(); s.buffer = b; s.connect(g); s.start(t);
    };
    switch (kind) {
      case "Cartoon Boing": osc("sine", 150, 900, 0.5); break;
      case "Frog Croak": osc("square", 120, 60, 0.35); break;
      case "Chicken Cluck": osc("square", 900, 500, 0.12); setTimeout(() => this.playSynth("Chicken Cluck"), 140); break;
      case "Explosion": case "Bass Drop": noise(0.8); osc("sine", 120, 30, 0.8); break;
      case "Car Horn": osc("sawtooth", 400, 400, 0.5); break;
      case "Trumpet Fail": case "Wrong Answer": [400, 380, 360, 300].forEach((f, i) => setTimeout(() => { this.ensure(); const o = c.createOscillator(); o.type = "sawtooth"; o.frequency.value = f; const gg = c.createGain(); gg.gain.value = 0.5; o.connect(gg); gg.connect(this.fxGain); o.start(); o.stop(c.currentTime + 0.22); }, i * 230)); break;
      case "Ghost Woo": osc("sine", 600, 200, 0.9); break;
      case "Robot Voice": osc("square", 200, 800, 0.4); break;
      case "Cat Meow": osc("sawtooth", 700, 1100, 0.35); break;
      case "Scream": osc("sawtooth", 900, 300, 0.7); break;
      default: osc("sine", 500, 900, 0.3);
    }
  }
  setChaos(level, fxList, onFx) {
    clearInterval(this.chaosTimer); this.chaosLevel = level;
    if (level === "OFF") return;
    const cfg = { LOW: [45000, 90000, 0.1], MEDIUM: [25000, 60000, 0.2], HIGH: [12000, 35000, 0.35], INSANE: [4000, 14000, 0.6] }[level] || [25000, 60000, 0.2];
    const loop = async () => {
      if (Math.random() < cfg[2] && fxList.length) {
        const fx = fxList[Math.floor(Math.random() * fxList.length)];
        onFx && onFx(fx);
      }
      this.chaosTimer = setTimeout(loop, cfg[0] + Math.random() * (cfg[1] - cfg[0]));
    };
    this.chaosTimer = setTimeout(loop, 5000);
  }
}
window.AudioEngine = AudioEngine;
