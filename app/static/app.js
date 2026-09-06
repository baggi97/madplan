/* Madplan — al interaktion går gennem små JSON-kald.
   Vi opdaterer UI'et med det samme og ruller tilbage hvis serveren siger nej. */

const UGE = document.body.dataset.uge;

/* Antal kald vi selv har i luften. Live-opdateringen holder fingrene væk
   imens, så den ikke ruller vores egen optimistiske ændring tilbage. */
let igangvaerende = 0;

async function send(sti, krop) {
  igangvaerende++;
  try {
    return await sendRaa(sti, krop);
  } finally {
    igangvaerende--;
  }
}

async function sendRaa(sti, krop) {
  const svar = await fetch(sti, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(krop || {}),
  });
  const data = await svar.json().catch(() => ({}));
  if (!svar.ok) throw new Error(data.fejl || "Noget gik galt");
  return data;
}

function varsel(tekst) {
  document.querySelector(".varsel")?.remove();
  const boks = document.createElement("div");
  boks.className = "varsel";
  boks.setAttribute("role", "status");
  boks.textContent = tekst;
  document.body.appendChild(boks);
  setTimeout(() => boks.remove(), 3200);
}

function opdaterTaeller(data) {
  const antal = typeof data === "number" ? data : data.antal;
  const taeller = document.querySelector("[data-taeller]");
  if (taeller) taeller.textContent = antal;
  const knap = document.querySelector('[data-handling="lav-madplan"]');
  if (knap) knap.disabled = antal === 0;

  /* Prisen er modellens skøn, ikke en beregning ud fra tilbudspriserne —
     derfor "ca." i skabelonen. */
  const boks = document.querySelector("[data-pris-boks]");
  if (boks && typeof data === "object" && data.pris !== undefined) {
    boks.querySelector("[data-pris]").textContent = data.pris;
    boks.hidden = antal === 0;
  }
}

/* --- Vælg retter --------------------------------------------------- */

/* Kortet er <li class="ret-kort">; knappen er kun den klikbare del, fordi
   portionsvælgeren ikke må ligge inde i en <button>. */
function bindVaelg(knap, sti, krop) {
  knap.addEventListener("click", async () => {
    const kort = knap.closest(".ret-kort");
    const var_valgt = kort.classList.contains("er-valgt");
    kort.classList.toggle("er-valgt");
    knap.setAttribute("aria-pressed", String(!var_valgt));

    try {
      opdaterTaeller(await send(sti, krop()));
    } catch (e) {
      kort.classList.toggle("er-valgt");
      knap.setAttribute("aria-pressed", String(var_valgt));
      varsel(e.message);
    }
  });
}

document.querySelectorAll(".ret[data-idx]").forEach((knap) =>
  bindVaelg(knap, `/api/uge/${UGE}/vaelg`, () => ({ idx: Number(knap.dataset.idx) }))
);

document.querySelectorAll(".ret[data-egen-idx]").forEach((knap) =>
  bindVaelg(knap, `/api/uge/${UGE}/egen/vaelg`, () => ({
    idx: Number(knap.dataset.egenIdx),
  }))
);

/* --- Antal personer pr. ret ---------------------------------------- */

const MIN_PORTIONER = 1;
const MAKS_PORTIONER = 12;

function opdaterTalKnapper(raekke) {
  const n = Number(raekke.querySelector("[data-portioner-tal]").textContent);
  raekke.querySelector('[data-portioner="ned"]').disabled = n <= MIN_PORTIONER;
  raekke.querySelector('[data-portioner="op"]').disabled = n >= MAKS_PORTIONER;
}

document.querySelectorAll(".portioner").forEach(opdaterTalKnapper);

document.querySelectorAll("[data-portioner]").forEach((knap) => {
  knap.addEventListener("click", async () => {
    const raekke = knap.closest(".portioner");
    const felt = raekke.querySelector("[data-portioner-tal]");
    const foer = Number(felt.textContent);
    const efter = knap.dataset.portioner === "op" ? foer + 1 : foer - 1;
    if (efter < MIN_PORTIONER || efter > MAKS_PORTIONER) return;

    felt.textContent = efter;
    opdaterTalKnapper(raekke);

    try {
      opdaterTaeller(
        await send(`/api/uge/${UGE}/portioner`, {
          slags: knap.dataset.slags,
          idx: Number(knap.dataset.idx),
          portioner: efter,
        })
      );
    } catch (e) {
      felt.textContent = foer;
      opdaterTalKnapper(raekke);
      varsel(e.message);
    }
  });
});

/* --- Ugedag pr. ret -------------------------------------------------- */

document.querySelectorAll("[data-dag]").forEach((vaelger) => {
  let foer = vaelger.value;
  vaelger.addEventListener("change", async () => {
    try {
      await send(`/api/uge/${UGE}/dag`, {
        slags: vaelger.dataset.slags,
        idx: Number(vaelger.dataset.idx),
        dag: vaelger.value,
      });
      foer = vaelger.value;
    } catch (e) {
      vaelger.value = foer;
      varsel(e.message);
    }
  });
});

/* --- Familiens egne retter ------------------------------------------ */

/* Tilføj og fjern genindlæser: en sletning omnummererer de øvrige egne
   retter, og at rette indeks i klienten ville være en fejlkilde. */
const egenForm = document.querySelector("[data-egen-form]");
if (egenForm) {
  egenForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const felt = egenForm.querySelector(".egen-felt");
    const navn = felt.value.trim();
    if (!navn) return;

    const knap = egenForm.querySelector("button");
    knap.disabled = true;
    try {
      await send(`/api/uge/${UGE}/egen`, { navn });
      location.reload();
    } catch (err) {
      knap.disabled = false;
      varsel(err.message);
      felt.focus();
    }
  });
}

document.querySelectorAll("[data-slet-egen]").forEach((knap) => {
  knap.addEventListener("click", async () => {
    knap.disabled = true;
    try {
      await send(`/api/uge/${UGE}/egen/slet`, { idx: Number(knap.dataset.sletEgen) });
      location.reload();
    } catch (e) {
      knap.disabled = false;
      varsel(e.message);
    }
  });
});

/* --- Indkøbsliste -------------------------------------------------- */

function opdaterFremskridt() {
  const felt = document.querySelector("[data-fremskridt]");
  if (!felt) return;
  const antal = document.querySelectorAll(".linje input[data-vare]:checked").length;
  felt.querySelector("strong").textContent = antal;
  felt.classList.toggle("er-faerdig", antal > 0 && antal === Number(felt.dataset.iAlt));
}

document.querySelectorAll(".linje input[data-vare]").forEach((felt) => {
  felt.addEventListener("change", async () => {
    const linje = felt.closest(".linje");
    linje.classList.toggle("er-krydset", felt.checked);
    opdaterFremskridt();
    try {
      await send(`/api/uge/${UGE}/kryds`, { vare: felt.dataset.vare });
    } catch (e) {
      felt.checked = !felt.checked;
      linje.classList.toggle("er-krydset", felt.checked);
      opdaterFremskridt();
      varsel(e.message);
    }
  });
});

/* --- Faner --------------------------------------------------------- */

document.querySelectorAll(".fane").forEach((fane) => {
  fane.addEventListener("click", () => {
    document.querySelectorAll(".fane").forEach((f) => {
      const aktiv = f === fane;
      f.classList.toggle("er-aktiv", aktiv);
      f.setAttribute("aria-selected", String(aktiv));
    });
    document.querySelectorAll(".fanepanel").forEach((panel) => {
      panel.hidden = panel.id !== `fane-${fane.dataset.fane}`;
    });
  });
});

/* --- Handlinger der starter et AI-kald ----------------------------- */

function visArbejder(tekst) {
  document.querySelectorAll(".hoved, .retter, .bundbjaelke, .besked, .faner, .fanepanel, .omgoer")
    .forEach((el) => (el.hidden = true));
  const boks = document.querySelector(".arbejder-boks");
  if (boks) {
    boks.hidden = false;
    if (tekst) boks.querySelector(".arbejder-tekst").textContent = tekst;
  }
  poll();
}

document.querySelectorAll("[data-handling]").forEach((knap) => {
  knap.addEventListener("click", async () => {
    const handling = knap.dataset.handling;
    knap.disabled = true;

    try {
      if (handling === "lav-madplan") {
        await send(`/api/uge/${UGE}/lav-madplan`);
        visArbejder("Skriver opskrifter og indkøbsliste…");
      } else if (handling === "hent-forslag") {
        await send("/api/hent-forslag", { gennemtving: knap.dataset.gennemtving === "1" });
        visArbejder("Kigger ugens tilbud igennem…");
      } else if (handling === "nulstil-kryds") {
        await send(`/api/uge/${UGE}/nulstil-kryds`);
        document.querySelectorAll(".linje input[data-vare]").forEach((f) => {
          f.checked = false;
          f.closest(".linje").classList.remove("er-krydset");
        });
        opdaterFremskridt();
        knap.disabled = false;
        varsel("Listen er ryddet");
      }
    } catch (e) {
      knap.disabled = false;
      varsel(e.message);
    }
  });
});

/* --- Statuspolling ------------------------------------------------- */

let poller = null;

async function poll() {
  if (poller) return;
  poller = setInterval(async () => {
    try {
      const svar = await fetch(`/api/uge/${UGE}/status`);
      const data = await svar.json();
      if (data.status !== "arbejder") {
        clearInterval(poller);
        location.reload();
      }
    } catch (_) {
      /* netværket kan blinke — vi prøver igen om to sekunder */
    }
  }, 2000);
}

if (document.querySelector(".arbejder-boks:not([hidden])")) poll();

/* --- Live: se hvad de andre vælger --------------------------------- */

/* Alle i huset vælger fra hver sin telefon, så siden skal vise de andres
   valg uden at man genindlæser. Vi patcher DOM'en frem for at reloade —
   ellers mister man sin plads på indkøbslisten midt i butikken.

   Tre hensyn:
   - Ingen polling når fanen er skjult. Telefoner ligger i lommen hele ugen.
   - Ingen patch mens vores eget kald er undervejs; ellers ruller vi vores
     egen optimistiske ændring tilbage et øjeblik.
   - Ændrer antallet af egne retter sig, kan vi ikke patche — de kort findes
     ikke i DOM'en. Så genindlæser vi, og kun der. */

async function opdaterFraServer() {
  if (document.hidden || igangvaerende > 0) return;
  let d;
  try {
    d = await (await fetch(`/api/uge/${UGE}/live`)).json();
  } catch (_) {
    return; // netværket kan blinke
  }

  if (d.status === "arbejder") return location.reload();

  const egneKort = document.querySelectorAll("[data-egen]");
  if (egneKort.length && d.antal_egne !== egneKort.length) return location.reload();

  const valgt = new Set(d.valgt);
  document.querySelectorAll(".ret[data-idx]").forEach((knap) => {
    saetValgt(knap, valgt.has(Number(knap.dataset.idx)));
  });
  const egneValgt = new Set(d.egne_valgt);
  document.querySelectorAll(".ret[data-egen-idx]").forEach((knap) => {
    saetValgt(knap, egneValgt.has(Number(knap.dataset.egenIdx)));
  });

  const krydset = new Set(d.afkrydset);
  document.querySelectorAll(".linje input[data-vare]").forEach((felt) => {
    const skal = krydset.has(felt.dataset.vare);
    if (felt.checked !== skal) {
      felt.checked = skal;
      felt.closest(".linje").classList.toggle("er-krydset", skal);
    }
  });
  opdaterFremskridt();
  opdaterTaeller(d);
}

function saetValgt(knap, skal) {
  const kort = knap.closest(".ret-kort");
  if (kort.classList.contains("er-valgt") === skal) return;
  kort.classList.toggle("er-valgt", skal);
  knap.setAttribute("aria-pressed", String(skal));
}

if (document.querySelector(".ret, .linje input[data-vare]")) {
  setInterval(opdaterFraServer, 5000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) opdaterFraServer();
  });
}

/* --- Bedøm retten --------------------------------------------------- */

/* "Fravalgt" siger intet om hvorfor — valgte man fire ud af ti, siger det
   intet om de seks andre. En tommel efter måltidet gør. Samme tryk igen
   fjerner bedømmelsen, så man kan fortryde. */

document.querySelectorAll("[data-bedoem]").forEach((boks) => {
  const navn = boks.dataset.bedoem;
  boks.querySelectorAll(".bedoem-knap").forEach((knap) => {
    knap.addEventListener("click", async () => {
      const foer = [...boks.querySelectorAll(".bedoem-knap")].map((k) =>
        k.classList.contains("er-valgt")
      );
      const slaar_fra = knap.classList.contains("er-valgt");
      boks.querySelectorAll(".bedoem-knap").forEach((k) => k.classList.remove("er-valgt"));
      if (!slaar_fra) knap.classList.add("er-valgt");

      try {
        await send(`/api/uge/${UGE}/bedoem`, {
          navn,
          vurdering: knap.dataset.vurdering,
        });
        varsel(slaar_fra ? "Bedømmelse fjernet" : "Tak — det husker vi");
      } catch (e) {
        boks.querySelectorAll(".bedoem-knap").forEach((k, i) => {
          k.classList.toggle("er-valgt", foer[i]);
        });
        varsel(e.message);
      }
    });
  });
});

/* --- Push-beskeder --------------------------------------------------- */

/* Kræver sikker kontekst. På http:// findes serviceWorker slet ikke i
   browseren, og så skjuler vi hele afsnittet frem for at love noget vi
   ikke kan holde. */

const pushBoks = document.querySelector("[data-push]");

function base64TilBytes(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raa = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raa, (c) => c.charCodeAt(0));
}

function visPush(tekst, knaptekst, deaktiveret) {
  pushBoks.querySelector("[data-push-under]").textContent = tekst;
  const knap = pushBoks.querySelector("[data-push-knap]");
  knap.textContent = knaptekst;
  knap.disabled = Boolean(deaktiveret);
}

async function opsaetPush() {
  if (!pushBoks) return;

  const muligt =
    "serviceWorker" in navigator && "PushManager" in window && window.isSecureContext;
  if (!muligt) return; // afsnittet forbliver skjult

  let reg;
  try {
    reg = await navigator.serviceWorker.register("/sw.js");
  } catch (e) {
    return; // uden worker ingen push — sig ikke noget, det er ikke brugerens skyld
  }

  const svar = await fetch("/api/push/noegle").then((r) => r.json());
  if (!svar.slaaet_til || !svar.noegle) return;

  pushBoks.hidden = false;
  let abonnement = await reg.pushManager.getSubscription();

  if (Notification.permission === "denied") {
    visPush("Beskeder er blokeret for dette website i browserens indstillinger.", "Blokeret", true);
    return;
  }
  if (abonnement) visPush("Beskeder er slået til på denne enhed.", "Slå fra");

  pushBoks.querySelector("[data-push-knap]").addEventListener("click", async () => {
    const knap = pushBoks.querySelector("[data-push-knap]");
    knap.disabled = true;
    try {
      if (abonnement) {
        await send("/api/push/afmeld", { endpoint: abonnement.endpoint });
        await abonnement.unsubscribe();
        abonnement = null;
        visPush("Søndag morgen, når ugens tilbud er hentet.", "Slå til");
        varsel("Beskeder slået fra");
      } else {
        const lov = await Notification.requestPermission();
        if (lov !== "granted") {
          visPush("Du sagde nej til beskeder. Slå dem til i browserens indstillinger.", "Slå til");
          return;
        }
        abonnement = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: base64TilBytes(svar.noegle),
        });
        await send("/api/push/abonner", abonnement.toJSON());
        visPush("Beskeder er slået til på denne enhed.", "Slå fra");
        varsel("Beskeder slået til");
      }
    } catch (e) {
      varsel(e.message || "Kunne ikke ændre beskeder");
    } finally {
      knap.disabled = false;
    }
  });
}

opsaetPush();
