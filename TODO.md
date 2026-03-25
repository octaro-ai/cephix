# TODO

- Add an optional delivery-override tool so the LLM can request a channel switch without coupling the kernel to concrete channels.
- Decide whether delivery override should be injected through robot construction or dynamically through the configured tool execution layer.
- Keep the current default simple:
  - use `event.reply_target` for reply-capable inputs
  - otherwise fall back to the robot's configured `default_output_target`
- Revisit dynamic tool availability once a real MCS/MCP-backed `ToolExecutionPort` is integrated.
- Mid term:
  - wire the persistent event, episode, profile, and procedure stores into the runtime path
  - ~~add a memory manager that assembles planning context from firmware plus selected memory layers~~ (done: DefaultContextAssembler with General Mode + System Tools)
  - ~~add policy-controlled memory updates so the robot can suggest firmware changes without mutating human-owned files directly~~ (done: procedure.propose tool, status: proposed -> human approves -> active)
  - connect the real LLM planner so it can use all mounted tools (current planner is a keyword-matching stub)
  - add a human-facing review interface for proposed procedures (approve/reject/edit)
  - add a heartbeat SOP example (heartbeat.check.v1) with configurable intervals and active hours
- Kernel-Architektur: Austauschbare Kernels (Option A → C Roadmap)

  **Architektur-Erkenntnis (ROS-inspiriert):**
  Der `Plan` ist cephix's Äquivalent zu ROS's `cmd_vel` – die gemeinsame Sprache zwischen
  Entscheidungsträger und Aktuator. Egal *wer* den Plan erstellt (LLM, Mensch, Skript),
  die Ausführung (ToolExecutor, MessageDelivery, Memory) ist identisch.

  Der Roboter ist die Infrastruktur, der Kernel ist das Gehirn. Das Gehirn kann ein LLM sein,
  ein deterministisches Programm oder ein Mensch – die Muskeln (Tools), das Gedächtnis (Memory)
  und die Sinne (Channels/Events) bleiben identisch.

  **Kernel-Ebene vs. Planner-Ebene:**
  Der `PlannerPort` ist bereits austauschbar, d.h. man könnte theoretisch nur den Planner
  tauschen, um verschiedene Entscheidungsmechanismen zu unterstützen. Allerdings bündelt der
  aktuelle `DigitalRobotKernel` den gesamten Observe→Plan→Execute→Respond-Zyklus. Für
  fundamental andere Abläufe (z.B. ein interaktiver Human-Operator, der nicht batch-artig
  arbeitet) reicht ein Planner-Tausch nicht aus – der gesamte Zyklus muss sich ändern können.
  Deshalb ist der Austausch auf Kernel-Ebene der richtige Ansatz.

  **Option A (aktuell implementiert): Kernel als Ganzes tauschen**
  - `KernelPort` Protocol definiert die minimale Schnittstelle
  - Jeder Kernel implementiert den vollständigen Event-Verarbeitungszyklus
  - Schnell lauffähig, einfach testbar (Mock-Kernels ohne LLM)
  - Nachteil: gemeinsame Phasen (Execute, Respond) werden pro Kernel dupliziert

  **Option C (langfristiges Ziel): Hybrid mit gemeinsamer Basis**
  Inspiriert von ros2_control `ControllerInterfaceBase`:
  ```
  KernelPort (Protocol: handle_event, state)

  BaseKernel (abstrakte Klasse mit gemeinsamen Phasen)
  ├── _observe()    → gemeinsam (ContextAssembler)
  ├── _execute()    → gemeinsam (ToolExecutor)
  ├── _respond()    → gemeinsam (MessageDelivery, Memory)
  ├── _decide()     → ABSTRAKT (jeder Kernel implementiert das anders)
  │
  ├── LLMKernel._decide()           → ruft PlannerPort auf
  ├── ScriptedKernel._decide()      → liest nächsten SOP-Schritt
  └── HumanOperatorKernel._decide() → wartet auf menschliche Eingabe
  ```
  Vorteile: kein duplizierter Code, saubere Trennung, alle Kernels teilen
  Observe/Execute/Respond und unterscheiden sich nur in der Entscheidungsphase.

  **Verworfen: Meta-Kernel / Kernel-pro-Event**
  Ein Meta-Kernel, der je nach Event-Typ an verschiedene Sub-Kernels delegiert, wurde
  als zu komplex verworfen. Stattdessen können Anwendungsfälle wie Human-in-the-Loop
  über spezifische Tools gelöst werden (z.B. "Call for a Human"-Tool, Freigabe-Tool).

  **Verworfen: Runtime-Lifecycle (activate/deactivate/switch)**
  ROS 2 Lifecycle Nodes bieten Zustandsübergänge für Hot-Swapping. Für cephix ist das
  nicht nötig – der Prozess ist schnell neu gestartet und konfiguriert.

  **Geplante Kernel-Typen:**
  - `DigitalRobotKernel` (LLM-gesteuert, aktuell vorhanden)
  - `DeterministicKernel` (führt ein Skript/SOP Schritt für Schritt aus, kein LLM)
  - `HumanOperatorKernel` (Mensch nutzt den Roboter wie ein Exoskelett: sieht den
    gleichen Kontext, die gleichen Tools, das gleiche Memory wie ein LLM)

- Long term:
  - define a formal robot brain package for cloning, transfer, and tenant-safe distribution
  - support backend adapters for alternative memory implementations without changing robot internals
  - add memory classification for cloneable, tenant-bound, and sensitive knowledge
  - add a `REFLECTING` state to the kernel (post-run self-evaluation, feeding back into procedural memory)
  - evaluate per-SOP autonomy level override (SOP metadata can set a max level for its workflow)
  - refactor from Option A to Option C (BaseKernel mit gemeinsamer Observe/Execute/Respond-Basis)
