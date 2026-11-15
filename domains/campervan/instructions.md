- Where knowledge.md and base-vans.md overlap on engines, base-vans.md is the more detailed one. Use base-vans.md for
  the Engine & gearbox score (PREFER 8-10, OK 6-7, CAUTION 3-5, AVOID 0-2; one level worse with no timing or injector
  history past 200k km), for the card's on-site checks (the rust spots and faults of this generation) and for the
  seller questions (e.g. the timing belt date on a 2.3 MultiJet, chain history on an OM651).
- Scores use the bands for each spec in knowledge.md (interpolate; apply its "watch" lines). A spec that is not shown
  at all gets no score; this overrides knowledge.md's "missing = max 3". Mentioned but vague (e.g. "solar" with no
  watts, "webasto" with no make): at most 3.
- model_family: when the make is known but the year sits on a generation boundary (e.g. a 2014 Ducato), pick the more
  likely generation from the year, the photos and the engine; "unknown" only when the make itself is unknown.
- price_net is true for "netto", "+VAT", "do odliczenia".
- Hard limits for criteria_checks (deal_breaker true): budget, maximum mileage, oldest year, maximum weight for the
  licence, refused base vehicles or engines.
- seller_message order: identity and papers first (VIN, first registration, DMC and empty weight, registered as a
  camper, number of registered seats (S.1), owner, import papers), then specs and equipment (exact model and layout, engine and gearbox, heating, gas,
  water, electrics, what the unknown criteria need), then history and condition (service history and timing belt,
  rust, damp, gas inspection), and last a request for missing photos. Do not ask about a viewing, a test drive, a
  diagnostic scan, a meeting or the price: those come later.
- Polish messages must not depend on the writer's grammatical gender ("Interesuje mnie...", "Czy mogę prosić o...",
  not "jestem zainteresowany"); use "Pan/Pani" only if the seller's name makes it clear, otherwise leave it out.
- seller_sms ends with a request to send photos on WhatsApp if photos are missing.
- "Registered seats" is what the registration allows (S.1), not the seats fitted; "4 miejsca" in a listing usually
  means sleeping places. Compare it with "Number of people travelling" in criteria.md.
