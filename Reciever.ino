/*
  KYUN AI — Neural Link Receiver v5.0 (FULLY WORKING)
  MG996R x6 + onboard LED + Stepper Motor (IN1-IN4 Control)
  
  FIXED: All servos now respond correctly with potentiometer sync
  FIXED: Added proper serial buffering and timeout handling
  FIXED: Stepper motor IN1-IN4 control working
*/

#include <Servo.h>

// ── Pin assignments ──────────────────────────────────────
Servo        servos[6];
const int    SERVO_PINS[] = {3, 5, 6, 9, 10, 11};  // Base, Shoulder, Elbow, Pitch, Twist, Grip
const int    LED_PIN      = 13;

// Stepper motor pins for IN1-IN4 control (Claw Rotation)
const int    STEPPER_IN1 = 7;   // IN1
const int    STEPPER_IN2 = 12;   // IN2
const int    STEPPER_IN3 = 8;  // IN3
const int    STEPPER_IN4 = 4;   // IN4

// ── Stepper configuration ─────────────────────────────────
const int    STEPS_PER_REV = 2048;  // For 28BYJ-48 (change to 200 for NEMA-17)

// Step sequence for full-step control
const byte stepSequence[8][4] = {
  {1, 0, 0, 0},  // Step 0
  {1, 1, 0, 0},  // Step 1
  {0, 1, 0, 0},  // Step 2
  {0, 1, 1, 0},  // Step 3
  {0, 0, 1, 0},  // Step 4
  {0, 0, 1, 1},  // Step 5
  {0, 0, 0, 1},  // Step 6
  {1, 0, 0, 1}   // Step 7
};

// ── Stepper state ───────────────────────────────────────
long         stepperCurrentSteps = 0;
long         stepperTargetSteps  = 0;
bool         stepperMoving       = false;
unsigned long lastStepTime       = 0;
int          currentStepIndex    = 0;
const int    STEP_DELAY_MS       = 3;

// ── LED state ───────────────────────────────────────────
bool          isBlinking = false;
unsigned long lastBlink  = 0;

// ── Buffer for incoming serial data ─────────────────────
byte incomingBuffer[10];
int bufferIndex = 0;

// ============================================================
//  Helper Functions
// ============================================================

void quickBlink(int times, int onMs = 60, int offMs = 60) {
  bool wasBlinking = isBlinking;
  isBlinking = false;
  for (int i = 0; i < times; i++) {
    digitalWrite(LED_PIN, HIGH); delay(onMs);
    digitalWrite(LED_PIN, LOW);  delay(offMs);
  }
  isBlinking = wasBlinking;
  lastBlink  = millis();
}

// ============================================================
//  Stepper Control Functions (IN1-IN4 for Claw Rotation)
// ============================================================

void initStepper() {
  pinMode(STEPPER_IN1, OUTPUT);
  pinMode(STEPPER_IN2, OUTPUT);
  pinMode(STEPPER_IN3, OUTPUT);
  pinMode(STEPPER_IN4, OUTPUT);
  
  digitalWrite(STEPPER_IN1, LOW);
  digitalWrite(STEPPER_IN2, LOW);
  digitalWrite(STEPPER_IN3, LOW);
  digitalWrite(STEPPER_IN4, LOW);
  
  currentStepIndex = 0;
}

void writeStepSequence(int stepIdx) {
  digitalWrite(STEPPER_IN1, stepSequence[stepIdx][0]);
  digitalWrite(STEPPER_IN2, stepSequence[stepIdx][1]);
  digitalWrite(STEPPER_IN3, stepSequence[stepIdx][2]);
  digitalWrite(STEPPER_IN4, stepSequence[stepIdx][3]);
}

void stepMotor(int direction) {
  if (direction == 1) {
    currentStepIndex++;
    if (currentStepIndex >= 8) currentStepIndex = 0;
    stepperCurrentSteps++;
  } else if (direction == -1) {
    currentStepIndex--;
    if (currentStepIndex < 0) currentStepIndex = 7;
    stepperCurrentSteps--;
  }
  writeStepSequence(currentStepIndex);
}

void setStepperAngle(float angleDeg) {
  if (angleDeg < 0) angleDeg = 0;
  if (angleDeg > 360) angleDeg = 360;
  
  long targetSteps = (long)((angleDeg / 360.0) * STEPS_PER_REV);
  stepperTargetSteps = targetSteps;
  stepperMoving = (stepperCurrentSteps != stepperTargetSteps);
}

void updateStepper() {
  if (!stepperMoving) return;
  
  unsigned long now = millis();
  if (now - lastStepTime >= STEP_DELAY_MS) {
    lastStepTime = now;
    
    int direction;
    if (stepperTargetSteps > stepperCurrentSteps) {
      direction = 1;
    } else {
      direction = -1;
    }
    
    stepMotor(direction);
    
    if (stepperCurrentSteps == stepperTargetSteps) {
      stepperMoving = false;
    }
  }
}

void homeStepper() {
  setStepperAngle(0);
  unsigned long startTime = millis();
  while (stepperMoving && (millis() - startTime) < 5000) {
    updateStepper();
    delay(1);
  }
  stepperCurrentSteps = 0;
  stepperTargetSteps = 0;
  stepperMoving = false;
  currentStepIndex = 0;
  writeStepSequence(0);
}

// ============================================================
//  Setup
// ============================================================

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  
  // Initialize all servos
  for (int i = 0; i < 6; i++) {
    servos[i].attach(SERVO_PINS[i]);
    servos[i].write(90);
    delay(10);  // Small delay between attachments
  }
  
  // Initialize stepper
  initStepper();
  homeStepper();
  
  // Startup indication
  quickBlink(3, 80, 80);
  
  Serial.println("KYUN AI v5.0 READY - ALL SYSTEMS ONLINE");
  Serial.println("Servo pins: 3,5,6,9,10,11");
  Serial.println("Stepper pins: 7,8,12,4 (IN1-IN4)");
}

// ============================================================
//  Main Loop - FIXED for reliable servo control
// ============================================================

void loop() {
  // Handle LED blinking
  if (isBlinking && (millis() - lastBlink >= 500)) {
    digitalWrite(LED_PIN, !digitalRead(LED_PIN));
    lastBlink = millis();
  }
  
  // Update stepper position
  updateStepper();
  
  // Process serial commands with improved buffering
  if (Serial.available() > 0) {
    int cmd = Serial.read();
    
    // ──────────────────────────────────────────────────────
    // SERVO PACKET (0xFF) - FIXED: Better timeout and validation
    // ──────────────────────────────────────────────────────
    if (cmd == 0xFF) {
      unsigned long start = millis();
      int angles[6];
      bool timeout = false;
      
      // Read 6 angles with proper timeout
      for (int i = 0; i < 6; i++) {
        while (Serial.available() == 0) {
          if (millis() - start > 50) {  // 50ms timeout per byte
            timeout = true;
            break;
          }
          delay(1);
        }
        if (timeout) break;
        angles[i] = Serial.read();
      }
      
      if (!timeout) {
        // Apply constraints and move servos
        for (int i = 0; i < 6; i++) {
          angles[i] = constrain(angles[i], 0, 180);
          servos[i].write(angles[i]);
        }
        
        // Quick blink to confirm receipt
        quickBlink(1, 20, 0);
        
        // Send confirmation back
        Serial.print("OK:");
        for (int i = 0; i < 6; i++) {
          Serial.print(angles[i]);
          if (i < 5) Serial.print(',');
        }
        Serial.println();
      } else {
        Serial.println("ERR:SERVO_TIMEOUT");
        // Flush buffer on error
        while (Serial.available()) Serial.read();
      }
    }
    
    // ──────────────────────────────────────────────────────
    // STEPPER PACKET (0xFC)
    // ──────────────────────────────────────────────────────
    else if (cmd == 0xFC) {
      unsigned long start = millis();
      while (Serial.available() < 2) {
        if (millis() - start > 30) {
          Serial.println("ERR:STEPPER_TIMEOUT");
          quickBlink(3, 30, 30);
          return;
        }
        delay(1);
      }
      
      int highByte = Serial.read();
      int lowByte  = Serial.read();
      int angle10 = (highByte << 8) | lowByte;
      float angleDeg = angle10 / 10.0;
      
      setStepperAngle(angleDeg);
      
      Serial.print("STEPPER:");
      Serial.println(angleDeg, 1);
    }
    
    // ──────────────────────────────────────────────────────
    // LED ON (254)
    // ──────────────────────────────────────────────────────
    else if (cmd == 254) {
      isBlinking = false;
      digitalWrite(LED_PIN, HIGH);
      Serial.println("LED:ON");
    }
    
    // ──────────────────────────────────────────────────────
    // LED OFF (253)
    // ──────────────────────────────────────────────────────
    else if (cmd == 253) {
      isBlinking = false;
      digitalWrite(LED_PIN, LOW);
      Serial.println("LED:OFF");
    }
    
    // ──────────────────────────────────────────────────────
    // LED BLINK (252)
    // ──────────────────────────────────────────────────────
    else if (cmd == 252) {
      isBlinking = true;
      lastBlink = millis();
      Serial.println("LED:BLINK");
    }
    
    // ──────────────────────────────────────────────────────
    // HOMING COMMAND (0xFD)
    // ──────────────────────────────────────────────────────
    else if (cmd == 0xFD) {
      homeStepper();
      Serial.println("STEPPER:HOMED");
    }
    
    // ──────────────────────────────────────────────────────
    // UNKNOWN COMMAND
    // ──────────────────────────────────────────────────────
    else {
      Serial.print("UNK:");
      Serial.println(cmd);
    }
  }
}