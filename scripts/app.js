const tableBody = document.querySelector('#events-table tbody');
const searchInput = document.querySelector('#search');
const alertElement = document.querySelector('#scrape-alert');

const moviesTableBody = document.querySelector('#movies-table tbody');
const moviesSearchInput = document.querySelector('#movies-search');
const moviesAlertElement = document.querySelector('#movies-alert');

const tabButtons = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('[role="tabpanel"]');

let allEvents = [];
let allMovies = [];

function toLocalDisplay(isoDateTime) {
  const date = new Date(isoDateTime);
  if (Number.isNaN(date.getTime())) {
    return 'Unknown scrape time';
  }
  return date.toLocaleString();
}

// --- Tab navigation ---

function activateTab(targetId) {
  tabButtons.forEach((btn) => {
    const active = btn.id === targetId;
    btn.classList.toggle('tab-btn--active', active);
    btn.setAttribute('aria-selected', String(active));
  });
  tabPanels.forEach((panel) => {
    panel.hidden = panel.getAttribute('aria-labelledby') !== targetId;
  });
}

tabButtons.forEach((btn) => {
  btn.addEventListener('click', () => activateTab(btn.id));
});

// --- Concerts ---

function renderEvents(events) {
  tableBody.innerHTML = '';

  if (!events.length) {
    const row = document.createElement('tr');
    row.innerHTML = '<td colspan="4">No events found.</td>';
    tableBody.appendChild(row);
    return;
  }

  for (const event of events) {
    const row = document.createElement('tr');

    const dateCell = document.createElement('td');
    dateCell.textContent = event.date || 'Unknown date';

    const bandsCell = document.createElement('td');
    bandsCell.textContent = event.bands || 'TBA';

    const venueCell = document.createElement('td');
    venueCell.textContent = event.venue || 'Unknown venue';

    const linkCell = document.createElement('td');
    if (event.link) {
      const link = document.createElement('a');
      link.href = event.link;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = 'Open show';
      linkCell.appendChild(link);
    } else {
      linkCell.textContent = 'N/A';
    }

    row.append(dateCell, bandsCell, venueCell, linkCell);
    tableBody.appendChild(row);
  }
}

function filterEvents() {
  const needle = searchInput.value.trim().toLowerCase();
  if (!needle) {
    renderEvents(allEvents);
    return;
  }

  const filtered = allEvents.filter((event) => (
    `${event.date} ${event.bands} ${event.venue}`.toLowerCase().includes(needle)
  ));
  renderEvents(filtered);
}

async function loadEvents() {
  try {
    const response = await fetch('data/events.json', { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const payload = await response.json();
    allEvents = Array.isArray(payload.events) ? payload.events : [];
    renderEvents(allEvents);

    if (payload.generated_at) {
      alertElement.textContent = `Last scraped: ${toLocalDisplay(payload.generated_at)}`;
    } else {
      alertElement.textContent = 'Last scraped: unknown';
    }
  } catch (error) {
    const hint = error.message.toLowerCase().includes('failed to fetch') || error.message === 'failed to fetch'
      ? ' – open the site via a local HTTP server (e.g. python -m http.server 8000) rather than directly from the filesystem'
      : '';
    alertElement.textContent = `Unable to load event data (${error.message})${hint}.`;
    renderEvents([]);
  }
}

searchInput.addEventListener('input', filterEvents);

// --- Movies ---

function renderMovies(movies) {
  moviesTableBody.innerHTML = '';

  if (!movies.length) {
    const row = document.createElement('tr');
    row.innerHTML = '<td colspan="5">No movies found.</td>';
    moviesTableBody.appendChild(row);
    return;
  }

  for (const movie of movies) {
    const row = document.createElement('tr');

    const dateCell = document.createElement('td');
    dateCell.textContent = movie.date || 'Unknown date';

    const titleCell = document.createElement('td');
    titleCell.textContent = movie.title || 'Unknown title';

    const genreCell = document.createElement('td');
    genreCell.textContent = movie.genre || 'N/A';

    const locationCell = document.createElement('td');
    locationCell.textContent = movie.location || 'Landmark Theatres';

    const ticketsCell = document.createElement('td');
    if (movie.link) {
      const link = document.createElement('a');
      link.href = movie.link;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = 'Buy tickets';
      ticketsCell.appendChild(link);
    } else {
      ticketsCell.textContent = 'N/A';
    }

    row.append(dateCell, titleCell, genreCell, locationCell, ticketsCell);
    moviesTableBody.appendChild(row);
  }
}

function filterMovies() {
  const needle = moviesSearchInput.value.trim().toLowerCase();
  if (!needle) {
    renderMovies(allMovies);
    return;
  }

  const filtered = allMovies.filter((movie) => (
    `${movie.date} ${movie.title} ${movie.genre} ${movie.location}`.toLowerCase().includes(needle)
  ));
  renderMovies(filtered);
}

async function loadMovies() {
  try {
    const response = await fetch('data/movies.json', { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const payload = await response.json();
    allMovies = Array.isArray(payload.movies) ? payload.movies : [];
    renderMovies(allMovies);

    if (payload.generated_at) {
      moviesAlertElement.textContent = `Last scraped: ${toLocalDisplay(payload.generated_at)}`;
    } else {
      moviesAlertElement.textContent = 'Last scraped: unknown';
    }
  } catch (error) {
    const hint = error.message.toLowerCase().includes('failed to fetch')
      ? ' – open the site via a local HTTP server (e.g. python -m http.server 8000) rather than directly from the filesystem'
      : '';
    moviesAlertElement.textContent = `Unable to load movie data (${error.message})${hint}.`;
    renderMovies([]);
  }
}

moviesSearchInput.addEventListener('input', filterMovies);

loadEvents();
loadMovies();

