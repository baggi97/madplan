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

/* --- Vælg retter --------------------------------------------------- */

document.querySelectorAll(".ret").forEach((kort) => {
  kort.addEventListener("click", async () => {
    const var_valgt = kort.classList.contains("er-valgt");
    kort.classList.toggle("er-valgt");
    kort.setAttribute("aria-pressed", String(!var_valgt));

    try {
      const data = await send(`/api/uge/${UGE}/vaelg`, { idx: Number(kort.dataset.idx) });
      const taeller = document.querySelector("[data-taeller]");
      if (taeller) {
        taeller.textContent = data.antal;
        const knap = document.querySelector('[data-handling="lav-madplan"]');
        if (knap) knap.disabled = data.antal === 0;
      }
    } catch (e) {
      kort.classList.toggle("er-valgt");
      kort.setAttribute("aria-pressed", String(var_valgt));
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
