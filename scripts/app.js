const tableBody = document.querySelector('#events-table tbody');
const searchInput = document.querySelector('#search');
const alertElement = document.querySelector('#scrape-alert');

let allEvents = [];

function toLocalDisplay(isoDateTime) {
  const date = new Date(isoDateTime);
  if (Number.isNaN(date.getTime())) {
    return 'Unknown scrape time';
  }
  return date.toLocaleString();
}

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
loadEvents();
