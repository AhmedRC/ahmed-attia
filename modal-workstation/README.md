# محطة العمل الإنشائية على Modal

جهاز لينكس قوي (Ubuntu 24.04 بواجهة XFCE4) على Modal. بيشتغل **وقت الحاجة بس** ويطفى لوحده لما يبقى فاضي.
بتتحكم فيه من **Hermes Agent** اللي شغّال دايماً على الـ VPS بتاعك.

```
تيليجرام / CLI ──► Hermes Agent (على VPS، شغّال دايماً)
                        │  أداة ws (Python + Modal SDK)
                        ▼
                ┌──────────── Modal ────────────┐
                │ Desktop sandbox (XFCE + noVNC) │◄── أنت من المتصفح
                │ Solver sandbox (بدون واجهة)    │
                │ Volume: /work (ملفاتك الدائمة) │
                └────────────────────────────────┘
```

## البرامج الموجودة
| الفئة | البرامج |
|---|---|
| الرسم والنمذجة | FreeCAD (FEM, BIM, Rebar)، LibreCAD |
| الشبكات والنتائج | Gmsh، ParaView |
| محركات الحل | CalculiX (`ccx`, `cgx`)، OpenMPI |
| بايثون إنشائي | openseespy، PyNiteFEA، anastruct، sectionproperties، gmsh، meshio، ezdxf، ccx2paraview |
| أدوات تانية | Firefox، Terminal، Thunar، خطوط عربي، لوحة مفاتيح عربي/إنجليزي (Alt+Shift) |

## المواصفات (Profiles)
في Modal الـ "core" معناها **نواة حقيقية = 2 vCPU**. والفلوس بتتحسب على **الأكبر بين اللي طلبته واللي استهلكته فعلاً**.
علشان كده بنطلب موارد صغيرة ونسمح للجهاز يعلى لحد سقف معيّن: لو الجهاز فاضي بتدفع سنتات، ولو السولفر شغّال بياخد كل الأنوية.

| Profile | السقف | فاضي (تقريباً) | تحميل كامل (تقريباً) |
|---|---|---|---|
| `light` | 4 أنوية / 16GB | ~0.19$/ساعة | ~0.95$/ساعة |
| `daily` | 8 أنوية / 32GB | ~0.24$/ساعة | ~1.9$/ساعة |
| `heavy` | 32 نواة / 64GB | ~0.48$/ساعة | ~6.1$/ساعة |

> ملحوظة: الأرقام مبنية على أسعار Modal Sandbox (أعلى 3 مرات من الـ Functions العادية). الاستهلاك الفعلي غالباً بيكون بين الرقمين.
> مثال: 60 ساعة شغل يومي على `daily` في الشهر (أغلبها رسم) + 10 تحليلات ثقيلة × 15 دقيقة ≈ 15–25$. يعني داخل الـ 30$ المجانية.

## التثبيت (على الـ VPS بتاع Hermes)
```bash
git clone -b modal-workstation https://github.com/AhmedRC/ahmed-attia.git
cd ahmed-attia/modal-workstation
bash install_vps.sh          # بيثبّت ws + modal + skill في ~/.hermes/skills
modal token new              # ربط حساب Modal
ws build                     # بناء الصورة أول مرة (10–15 دقيقة، وبعدها بتتخزن)
```
بعد كده:
- **حط حد أقصى للصرف** في Modal (Settings ← Usage) = 30$.
- اعمل restart لـ Hermes gateway علشان يشوف الـ skill الجديدة وأمر `ws`.
- لازم يكون الـ terminal backend في Hermes هو `local` (أو أي backend تاني متسطّب عليه `ws`).

## الاستخدام من تيليجرام (أمثلة)
- «شغل الديسكتوب» ← هيرجعلك لينك تفتحه من المتصفح.
- «حل ملف frame.py على وضع heavy وابعتلي النتايج»
- «إيه الشغّال دلوقتي وبكام؟»
- «اقفل الجهاز»

## الأوامر يدوياً
```bash
ws desktop start --profile daily --idle 20 --hours 8
ws desktop url
ws desktop stop
ws run "python model.py" --cwd projects/tower --profile heavy
ws run "ccx -i beam" --cwd projects/beam --detach   # للمهام الطويلة
ws job-log job-20261113-183000
ws push ./model.FCStd projects/tower/
ws pull projects/tower/results ./results
ws ls projects -r
ws status
ws stop --all
```

## إزاي بنوفّر الفلوس
1. **Watchdog جوه الجهاز:** بيطفّيه لو مفيش متصفح متوصل، والمعالج فاضي، لمدة `--idle` دقيقة (الافتراضي 20).
2. **سقف للعمر:** `--hours` (الافتراضي 8 ساعات، وأقصى حاجة 24).
3. **طلب موارد صغير مع سماح بالزيادة:** بتدفع على الاستهلاك الفعلي بس.
4. **Hermes نفسه:** الـ skill بتقوله يقفل أي جهاز لما تخلص، ويشيّك بـ `ws status`.

## التخزين
- `/work` هو Modal Volume دائم (أول 1TB مجاناً). مشاريعك في `/work/projects` والملفات اللي بترفعها في `/work/inbox`.
- إعدادات XFCE و FreeCAD بتتحفظ في `/work/.ws-home`.
- سجلات المهام في `/work/.ws-jobs/*.log`.
- أي حاجة برا `/work` بتتمسح لما الجهاز يطفى.

## الأمان
- الاتصال مشفّر (TLS) عن طريق Modal tunnel، وعليه باسورد VNC عشوائي بيتغيّر مع كل تشغيل.
- اللينك فيه الباسورد، فاعتبره سر. لو اتسرّب: `ws desktop stop` وبعدين شغّل من جديد.

## ملاحظات تقنية
- الرسوميات بتشتغل على المعالج (Mesa llvmpipe) لأن مفيش GPU. ده كفاية لـ FreeCAD و ParaView و Gmsh، وبيبقى أسرع كل ما الأنوية تزيد.
- ده Container مش VM: مفيش systemd ولا snap، وكل حاجة شغّالة بصلاحيات root جوه الـ sandbox المعزول.
- لو شغّلت مهمة في sandbox منفصل والديسكتوب مفتوح، `ws` بيعمل reload للـ volume في الديسكتوب لوحده علشان النتايج تظهر.
