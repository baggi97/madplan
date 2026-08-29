/* Madplan — al interaktion går gennem små JSON-kald.
   Vi opdaterer UI'et med det samme og ruller tilbage hvis serveren siger nej. */

const UGE = document.body.dataset.uge;

async function send(sti, krop) {
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

function opdaterTaeller(antal) {
  const taeller = document.querySelector("[data-taeller]");
  if (!taeller) return;
  taeller.textContent = antal;
  const knap = document.querySelector('[data-handling="lav-madplan"]');
  if (knap) knap.disabled = antal === 0;
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
      opdaterTaeller((await send(sti, krop())).antal);
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
      await send(`/api/uge/${UGE}/portioner`, {
        slags: knap.dataset.slags,
        idx: Number(knap.dataset.idx),
        portioner: efter,
      });
    } catch (e) {
      felt.textContent = foer;
      opdaterTalKnapper(raekke);
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
