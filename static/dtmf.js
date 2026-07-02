class TonePair {
    constructor(context, destNode, toneEncodings, digit, duration, at) {
        let freqs = toneEncodings[digit];

        this.osc1 = context.createOscillator();
        this.osc2 = context.createOscillator();
        this.osc1.frequency.value = freqs.f1;
        this.osc2.frequency.value = freqs.f2;
    
        this.osc1.connect(destNode);
        this.osc2.connect(destNode);
    
        if (duration) {
            this.osc1.start(at);
            this.osc2.start(at);
            this.osc1.stop(at + duration);
            this.osc2.stop(at + duration);
        }
    }

    start(duration = undefined, at = 0) {
        this.osc1.start(at);
        this.osc2.start(at);
        if (duration) {
            this.osc1.stop(at + duration);
            this.osc2.stop(at + duration);
        }
    }

    stop() {
        this.osc1.stop(0);
        this.osc2.stop(0);
    }
}

export class DTMF extends TonePair {
    static toneEncodings = {
        "1": {f1: 697, f2: 1209},
        "2": {f1: 697, f2: 1336},
        "3": {f1: 697, f2: 1477},
        "4": {f1: 770, f2: 1209},
        "5": {f1: 770, f2: 1336},
        "6": {f1: 770, f2: 1477},
        "7": {f1: 852, f2: 1209},
        "8": {f1: 852, f2: 1336},
        "9": {f1: 852, f2: 1477},
        "*": {f1: 941, f2: 1209},
        "0": {f1: 941, f2: 1336},
        "#": {f1: 941, f2: 1477},
        "A": {f1: 697, f2: 1633},
        "B": {f1: 770, f2: 1633},
        "C": {f1: 852, f2: 1633},
        "D": {f1: 941, f2: 1633},
        "E": {f1: 941, f2: 1209}, // alias of *
        "F": {f1: 941, f2: 1477}  // alias of #
    }

    constructor(context, destNode, digit, duration = 0, at = 0) {
        super(context, destNode, DTMF.toneEncodings, digit, duration, at);
    }
}

export class MF extends TonePair {
    static toneEncodings = {
        "1":   {f1: 700,  f2: 900},
        "2":   {f1: 700,  f2: 1100},
        "3":   {f1: 900,  f2: 1100},
        "4":   {f1: 700,  f2: 1300},
        "5":   {f1: 900,  f2: 1300},
        "6":   {f1: 1100, f2: 1300},
        "7":   {f1: 700,  f2: 1500},
        "8":   {f1: 900,  f2: 1500},
        "9":   {f1: 1100, f2: 1500},
        "0":   {f1: 1300, f2: 1500},
        "ST":  {f1: 1500, f2: 1700},
        "ST2": {f1: 900,  f2: 1700},
        "ST3": {f1: 700,  f2: 1700},
        "KP":  {f1: 1100, f2: 1700},
        "KP2": {f1: 1300, f2: 1700},

        "S":   {f1: 1500, f2: 1700}, // alias of ST
        "T":   {f1: 900,  f2: 1700}, // alias of ST2
        "U":   {f1: 700,  f2: 1700}, // alias of ST3
        "K":   {f1: 1100, f2: 1700}, // alias of KP
        "L":   {f1: 1300, f2: 1700}  // alias of KP2
    }

    constructor(context, destNode, digit, duration = 0, at = 0) {
        super(context, destNode, MF.toneEncodings, digit, duration, at);
    }
}

export class Dialer {
    constructor(onDuration, offDuration) {
        this.onDuration = onDuration;
        this.offDuration = offDuration;
        this.setupAudioContext();
    }

    setupAudioContext() {
        let AudioContext = window.AudioContext ||
            window.webkitAudioContext || window.mozAudioContext;
        this.audioContext = new AudioContext();

        this.gainNode = this.audioContext.createGain();
        this.gainNode.gain.value = 0.4;

        let filterNode = this.audioContext.createBiquadFilter();
        filterNode.type = "lowpass";
        filterNode.frequency.value = 3600;

        this.gainNode.connect(filterNode);
        filterNode.connect(this.audioContext.destination);
    }

    dial(tonePairType, str) {
        // The AudioContext clock keeps advancing, so schedule relative to "now".
        // A fixed 0 base means every call after the first schedules tones in the
        // past, which Web Audio renders as silence.
        if (this.audioContext.state === 'suspended') {
            this.audioContext.resume();
        }
        let digitStartTime = this.audioContext.currentTime;

        for (let i = 0; i < str.length; i++) {
            new tonePairType(this.audioContext, this.gainNode, 
                str.charAt(i), this.onDuration, digitStartTime);
            digitStartTime += this.onDuration + this.offDuration;
        } 
    }

    dialDTMF(str) {
        this.dial(DTMF, str);
    }

    dialMF(str) {
        this.dial(MF, str);
    }
}