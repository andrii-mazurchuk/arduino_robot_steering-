/*
  Secure-ish PC <-> Arduino serial protocol (minimal ACK/NACK + checksum)
  -----------------------------------------------------------------------
  Frame format (ASCII, fixed markers):
    ^<SEQ>|<CMD>|<PAYLOAD>*<CS>$

    - '^' start-of-frame, '$' end-of-frame
    - <SEQ>: two uppercase hex digits 00..FF
    - <CMD>: PING, HELP, STATUS, V, M, R, S, B, I
    - <PAYLOAD>: optional (numbers or empty). No '|' or '*' allowed.
    - '*' separates content from checksum
    - <CS>: two uppercase hex digits, 8-bit XOR of all bytes in <SEQ>|<CMD>|<PAYLOAD>
            (exactly the substring between '^' and '*' — not including '^' or '*')

  Responses (Arduino -> PC) use the same frame format:
    - ACK:  ^<SEQ>|ACK|<INFO>*<CS>$         (INFO may be "OK" or data like "B:123")
    - NACK: ^<SEQ>|NACK|<ERROR>*<CS>$       (ERROR like "BAD_CS", "BAD_CMD", ...)

  Notes:
    - Parser resynchronizes on '^' and discards oversized/invalid frames.
    - Motion is time-based demo only; replace with encoder/PID later.
*/

// === Pin mapping (from your attachments) ===
#define PIN_LEFT_MOTOR_SPEED    5
#define PIN_LEFT_MOTOR_FORWARD  A0
#define PIN_LEFT_MOTOR_REVERSE  A1
#define PIN_LEFT_ENCODER        2

#define PIN_RIGHT_MOTOR_SPEED   6
#define PIN_RIGHT_MOTOR_FORWARD A3
#define PIN_RIGHT_MOTOR_REVERSE A2
#define PIN_RIGHT_ENCODER       3

#define TRIGER_PIN              11
#define ECHO_PIN                12
#define ANALOG_READ_IR_LEFT     A4
#define DIGITAL_READ_IR_LEFT    7
#define ANALOG_READ_IR_RIGHT    A5
#define DIGITAL_READ_IR_RIGHT   8

#define SERIAL_BAUD_RATE        9600

// === Motion config (demo) ===
int basePWM = 140;
int lastDir = 0; // -1 rev, 0 stop, +1 fwd
const float MS_PER_CM  = 18.0; // tune for your robot
const float MS_PER_DEG = 6.0;  // tune for your robot

// === Parser state ===
static const int MAX_FRAME = 96;
char frameBuf[MAX_FRAME];
int frameLen = 0;
bool inFrame = false;

// === Utils ===
void stopMotors() {
  digitalWrite(PIN_LEFT_MOTOR_FORWARD, LOW);
  digitalWrite(PIN_LEFT_MOTOR_REVERSE, LOW);
  analogWrite(PIN_LEFT_MOTOR_SPEED, 0);

  digitalWrite(PIN_RIGHT_MOTOR_FORWARD, LOW);
  digitalWrite(PIN_RIGHT_MOTOR_REVERSE, LOW);
  analogWrite(PIN_RIGHT_MOTOR_SPEED, 0);

  lastDir = 0;
}

void driveStraight(int dir, int pwm) {
  if (dir >= 0) {
    digitalWrite(PIN_LEFT_MOTOR_FORWARD, HIGH);
    digitalWrite(PIN_LEFT_MOTOR_REVERSE, LOW);
    digitalWrite(PIN_RIGHT_MOTOR_FORWARD, HIGH);
    digitalWrite(PIN_RIGHT_MOTOR_REVERSE, LOW);
    lastDir = 1;
  } else {
    digitalWrite(PIN_LEFT_MOTOR_FORWARD, LOW);
    digitalWrite(PIN_LEFT_MOTOR_REVERSE, HIGH);
    digitalWrite(PIN_RIGHT_MOTOR_FORWARD, LOW);
    digitalWrite(PIN_RIGHT_MOTOR_REVERSE, HIGH);
    lastDir = -1;
  }
  analogWrite(PIN_LEFT_MOTOR_SPEED, constrain(pwm, 0, 255));
  analogWrite(PIN_RIGHT_MOTOR_SPEED, constrain(pwm, 0, 255));
}

void rotateInPlace(int dir, int pwm) {
  if (dir >= 0) {
    digitalWrite(PIN_LEFT_MOTOR_FORWARD, HIGH);
    digitalWrite(PIN_LEFT_MOTOR_REVERSE, LOW);
    digitalWrite(PIN_RIGHT_MOTOR_FORWARD, LOW);
    digitalWrite(PIN_RIGHT_MOTOR_REVERSE, HIGH);
  } else {
    digitalWrite(PIN_LEFT_MOTOR_FORWARD, LOW);
    digitalWrite(PIN_LEFT_MOTOR_REVERSE, HIGH);
    digitalWrite(PIN_RIGHT_MOTOR_FORWARD, HIGH);
    digitalWrite(PIN_RIGHT_MOTOR_REVERSE, LOW);
  }
  analogWrite(PIN_LEFT_MOTOR_SPEED, constrain(pwm, 0, 255));
  analogWrite(PIN_RIGHT_MOTOR_SPEED, constrain(pwm, 0, 255));
  lastDir = 0;
}

long sonarDistanceCM() {
  digitalWrite(TRIGER_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIGER_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIGER_PIN, LOW);

  long duration = pulseIn(ECHO_PIN, HIGH, 30000UL);
  if (duration == 0) return 0;
  long distance = duration * 0.034 / 2.0;
  // if (distance < 0) distance = 0;
  return distance;
}

void readIR(int &al, int &ar, int &dl, int &dr) {
  al = analogRead(ANALOG_READ_IR_LEFT);
  ar = analogRead(ANALOG_READ_IR_RIGHT);
  dl = digitalRead(DIGITAL_READ_IR_LEFT);
  dr = digitalRead(DIGITAL_READ_IR_RIGHT);
}

uint8_t hex2nibble(char c) {
  if (c >= '0' && c <= '9') return (uint8_t)(c - '0');
  if (c >= 'A' && c <= 'F') return (uint8_t)(10 + c - 'A');
  if (c >= 'a' && c <= 'f') return (uint8_t)(10 + c - 'a');
  return 0xFF;
}

uint8_t parseHex2(const char* p) {
  uint8_t hi = hex2nibble(p[0]);
  uint8_t lo = hex2nibble(p[1]);
  if (hi == 0xFF || lo == 0xFF) return 0xFF;
  return (uint8_t)((hi << 4) | lo);
}

void toHex2(uint8_t v, char out[3]) {
  const char* hex = "0123456789ABCDEF";
  out[0] = hex[(v >> 4) & 0x0F];
  out[1] = hex[v & 0x0F];
  out[2] = '\0';
}

uint8_t xorChecksum(const char* s, int len) {
  uint8_t cs = 0;
  for (int i = 0; i < len; ++i) cs ^= (uint8_t)s[i];
  return cs;
}

void sendFrame(const char* seq2, const char* cmd, const char* payload) {
  // Build content: <SEQ>|<CMD>|<PAYLOAD>
  char content[MAX_FRAME];
  content[0] = 0;
  // ensure payload non-null
  if (!payload) payload = "";
  // assemble
  snprintf(content, sizeof(content), "%s|%s|%s", seq2, cmd, payload);
  uint8_t cs = xorChecksum(content, strlen(content));
  char csHex[3]; toHex2(cs, csHex);

  Serial.write('^');
  Serial.print(content);
  Serial.write('*');
  Serial.print(csHex);
  Serial.write('$');
}

void sendACK(const char* seq2, const char* info) {
  sendFrame(seq2, "ACK", info ? info : "OK");
}

void sendNACK(const char* seq2, const char* err) {
  sendFrame(seq2, "NACK", err ? err : "ERR");
}

// === Command handling ===
void handleCommand(const char* seq2, const char* cmd, const char* payload) {
  // PING
  if (strcasecmp(cmd, "PING") == 0) {
    sendACK(seq2, "PONG");
    return;
  }
  // HELP
  if (strcasecmp(cmd, "HELP") == 0) {
    sendACK(seq2, "CMDS:PING,HELP,STATUS,V,M,R,S,B,I");
    return;
  }
  // STATUS
  if (strcasecmp(cmd, "STATUS") == 0) {
    char info[48];
    const char* d = (lastDir > 0) ? "fwd" : (lastDir < 0) ? "rev" : "stop";
    snprintf(info, sizeof(info), "STATUS:V=%d,DIR=%s", basePWM, d);
    sendACK(seq2, info);
    return;
  }
  // V:<0..255>
  if (strcasecmp(cmd, "V") == 0) {
    long v = atol(payload);
    if (v < 0 || v > 255) { sendNACK(seq2, "BAD_V"); return; }
    basePWM = (int)v;
    sendACK(seq2, "OK");
    return;
  }
  // M:<cm>
  if (strcasecmp(cmd, "M") == 0) {
    long cm = atol(payload);
    int dir = (cm >= 0) ? +1 : -1;
    unsigned long ms = (unsigned long)(labs(cm) * MS_PER_CM);
    driveStraight(dir, basePWM);
    delay(ms);
    stopMotors();
    sendACK(seq2, "OK");
    return;
  }
  // R:<deg>
  if (strcasecmp(cmd, "R") == 0) {
    long deg = atol(payload);
    int dir = (deg >= 0) ? +1 : -1;
    unsigned long ms = (unsigned long)(labs(deg) * MS_PER_DEG);
    rotateInPlace(dir, basePWM);
    delay(ms);
    stopMotors();
    sendACK(seq2, "OK");
    return;
  }
  // S
  if (strcasecmp(cmd, "S") == 0) {
    stopMotors();
    sendACK(seq2, "OK");
    return;
  }
  // B
  if (strcasecmp(cmd, "B") == 0) {
    long d = sonarDistanceCM();
    char info[16];
    snprintf(info, sizeof(info), "B:%ld", d);
    sendACK(seq2, info);
    return;
  }
  // I
  if (strcasecmp(cmd, "I") == 0) {
    int al, ar, dl, dr;
    readIR(al, ar, dl, dr);
    char info[48];
    snprintf(info, sizeof(info), "I:AL=%d,AR=%d,DL=%d,DR=%d", al, ar, dl, dr);
    sendACK(seq2, info);
    return;
  }

  sendNACK(seq2, "BAD_CMD");
}

// === Frame parsing ===
void tryProcessFrame() {
  // Expect: ^<SEQ>|<CMD>|<PAYLOAD>*<CS>$ in frameBuf[0..frameLen-1] including '^'..'$'
  if (frameLen < 7) return; // too short

  if (frameBuf[0] != '^' || frameBuf[frameLen - 1] != '$') return;

  // find '*'
  int star = -1;
  for (int i = 1; i < frameLen - 1; ++i) {
    if (frameBuf[i] == '*') { star = i; break; }
  }
  if (star < 0 || (frameLen - 1 - star) != 3) return; // need 2 hex + '$'

  // parse CS
  uint8_t gotCS = parseHex2(&frameBuf[star + 1]);
  if (gotCS == 0xFF) return;

  // compute CS over content between '^' and '*'
  int contentStart = 1;
  int contentLen = star - contentStart;
  if (contentLen <= 0) return;
  uint8_t calcCS = xorChecksum(&frameBuf[contentStart], contentLen);
  if (calcCS != gotCS) {
    // we don't know seq yet; try to extract it to NACK back
    // content looks like "<SEQ>|..."
    char seq2[3] = "00";
    if (contentLen >= 2) { seq2[0] = frameBuf[1]; seq2[1] = frameBuf[2]; }
    sendNACK(seq2, "BAD_CS");
    return;
  }

  // split content: <SEQ>|<CMD>|<PAYLOAD>
  // make a temporary null-terminated copy
  char tmp[MAX_FRAME];
  memcpy(tmp, &frameBuf[contentStart], contentLen);
  tmp[contentLen] = '\0';

  // tokenize by '|'
  char *p = tmp;
  char *seq2 = strtok(p, "|");
  char *cmd  = strtok(NULL, "|");
  char *payload = strtok(NULL, "|"); // may be NULL
  if (!seq2 || !cmd || payload == NULL) { // payload required (may be empty string)
    // craft minimal seq for NACK
    char sq[3] = "00";
    if (seq2 && strlen(seq2) == 2) { sq[0] = seq2[0]; sq[1] = seq2[1]; }
    sendNACK(sq, "BAD_FMT");
    return;
  }

  // validate SEQ format (two hex)
  if (!(strlen(seq2) == 2 &&
        hex2nibble(seq2[0]) != 0xFF &&
        hex2nibble(seq2[1]) != 0xFF)) {
    sendNACK("00", "BAD_SEQ");
    return;
  }

  handleCommand(seq2, cmd, payload);
}

void setup() {
  // Motors
  pinMode(PIN_LEFT_MOTOR_SPEED, OUTPUT);
  pinMode(PIN_LEFT_MOTOR_FORWARD, OUTPUT);
  pinMode(PIN_LEFT_MOTOR_REVERSE, OUTPUT);
  pinMode(PIN_RIGHT_MOTOR_SPEED, OUTPUT);
  pinMode(PIN_RIGHT_MOTOR_FORWARD, OUTPUT);
  pinMode(PIN_RIGHT_MOTOR_REVERSE, OUTPUT);
  stopMotors();

  // Sonar
  pinMode(TRIGER_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);

  // IR
  pinMode(ANALOG_READ_IR_LEFT, INPUT);
  pinMode(DIGITAL_READ_IR_LEFT, INPUT);
  pinMode(ANALOG_READ_IR_RIGHT, INPUT);
  pinMode(DIGITAL_READ_IR_RIGHT, INPUT);

  // Encoders (not used in this demo)
  pinMode(PIN_LEFT_ENCODER, INPUT_PULLUP);
  pinMode(PIN_RIGHT_ENCODER, INPUT_PULLUP);

  Serial.begin(SERIAL_BAUD_RATE);
  while (!Serial) { ; }
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (!inFrame) {
      if (c == '^') {
        inFrame = true;
        frameLen = 0;
        frameBuf[frameLen++] = c;
      }
      // else ignore until start marker
    } else {
      // inside a frame
      if (frameLen < MAX_FRAME) {
        frameBuf[frameLen++] = c;
      } else {
        // overflow -> reset
        inFrame = false;
        frameLen = 0;
      }
      if (c == '$') {
        // complete frame
        inFrame = false;
        tryProcessFrame();
        frameLen = 0;
      }
    }
  }
}
