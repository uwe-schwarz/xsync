const state = {
  archiveRoot: "",
  generatedAt: "",
  posts: [],
  filteredPosts: [],
  filter: "all",
  search: "",
  selectedId: null,
  detailsById: new Map(),
};

const timelineList = document.querySelector("#timeline-list");
const searchInput = document.querySelector("#search");
const statsRoot = document.querySelector("#stats");
const archiveRootNode = document.querySelector("#archive-root");
const generatedAtNode = document.querySelector("#generated-at");
const resultTitle = document.querySelector("#result-title");
const resultCount = document.querySelector("#result-count");
const detailTitle = document.querySelector("#detail-title");
const detailOpen = document.querySelector("#detail-open");
const detailBody = document.querySelector("#detail-body");
const cardTemplate = document.querySelector("#timeline-card-template");

async function bootstrap() {
  attachEvents();
  const response = await fetch("/api/posts");
  if (!response.ok) {
    renderFailure("Could not load archived posts.");
    return;
  }
  const payload = await response.json();
  state.archiveRoot = payload.archiveRoot;
  state.generatedAt = payload.generatedAt;
  state.posts = payload.posts;
  renderStats(payload.stats);
  archiveRootNode.textContent = payload.archiveRoot;
  generatedAtNode.textContent = `Indexed ${formatDateTime(payload.generatedAt)}`;
  state.selectedId = state.posts[0]?.id ?? null;
  applyFilters();
  if (state.selectedId) {
    await selectPost(state.selectedId, false);
  }
}

function attachEvents() {
  searchInput.addEventListener("input", (event) => {
    state.search = event.target.value.trim().toLowerCase();
    applyFilters();
  });

  document.querySelector("#filter-segment").addEventListener("click", (event) => {
    const button = event.target.closest("[data-filter]");
    if (!button) {
      return;
    }
    document.querySelectorAll(".segment").forEach((node) => {
      node.classList.toggle("is-active", node === button);
    });
    state.filter = button.dataset.filter;
    applyFilters();
  });
}

function renderStats(stats) {
  statsRoot.replaceChildren(
    ...[
      ["Posts", stats.posts],
      ["Bookmarks", stats.bookmarks],
      ["Threads", stats.threads],
      ["Authors", stats.authors],
      ["Media", stats.media],
    ].map(([label, value]) => {
      const row = document.createElement("div");
      row.className = "stats-row";
      row.innerHTML = `<dt>${label}</dt><dd>${formatNumber(value)}</dd>`;
      return row;
    }),
  );
}

function applyFilters() {
  state.filteredPosts = state.posts.filter((post) => {
    if (state.filter === "bookmarked" && !post.bookmarked) {
      return false;
    }
    if (state.filter === "threads" && !post.hasThread) {
      return false;
    }
    if (!state.search) {
      return true;
    }
    const haystack = [
      post.id,
      post.conversationId,
      post.author.name,
      post.author.username,
      post.text,
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(state.search);
  });

  if (!state.filteredPosts.some((post) => post.id === state.selectedId)) {
    state.selectedId = state.filteredPosts[0]?.id ?? null;
  }

  renderTimeline();

  if (state.selectedId) {
    selectPost(state.selectedId, false);
  } else {
    renderEmptyDetail("No posts match the current filter.");
  }
}

function renderTimeline() {
  const count = state.filteredPosts.length;
  resultTitle.textContent = state.filter === "all" ? "All archived posts" : labelForFilter();
  resultCount.textContent = `${formatNumber(count)} visible`;
  if (!count) {
    timelineList.innerHTML = `<div class="empty-state">No matching posts.</div>`;
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const post of state.filteredPosts) {
    const node = cardTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.postId = post.id;
    node.classList.toggle("is-selected", post.id === state.selectedId);
    setAvatar(node.querySelector(".post-avatar"), post.author);
    node.querySelector(".author-name").textContent = post.author.name || "Unknown";
    node.querySelector(".author-handle").textContent = post.author.username
      ? `@${post.author.username}`
      : "";
    node.querySelector(".author-verified").classList.toggle("hidden", !post.author.verified);
    node.querySelector(".post-time").textContent = formatDate(post.createdAt);
    node.querySelector(".post-text").innerHTML = renderText(post.text);
    fillMedia(node.querySelector(".post-media"), post.media, false);
    fillTags(node.querySelector(".post-tags"), post);
    fillMetrics(node.querySelector(".metric-row"), post.metrics);

    node.addEventListener("click", () => selectPost(post.id, true));
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectPost(post.id, true);
      }
    });
    fragment.append(node);
  }
  timelineList.replaceChildren(fragment);
}

async function selectPost(postId, scrollIntoView) {
  state.selectedId = postId;
  document.querySelectorAll(".post-card").forEach((node) => {
    node.classList.toggle("is-selected", node.dataset.postId === postId);
  });
  if (scrollIntoView) {
    document.querySelector(`[data-post-id="${CSS.escape(postId)}"]`)?.scrollIntoView({
      block: "nearest",
      behavior: "smooth",
    });
  }

  if (!state.detailsById.has(postId)) {
    const response = await fetch(`/api/posts/${postId}`);
    if (!response.ok) {
      renderFailure("Could not load the selected thread.");
      return;
    }
    state.detailsById.set(postId, await response.json());
  }
  renderDetail(state.detailsById.get(postId));
}

function renderDetail(detail) {
  const { post, thread } = detail;
  detailTitle.textContent = post.author.username ? `@${post.author.username}` : post.id;
  detailOpen.href = post.url;
  detailOpen.classList.remove("hidden");

  const hero = document.createElement("section");
  hero.className = "detail-hero";
  hero.innerHTML = `
    <div class="detail-topline">
      <div class="detail-author">
        ${renderAvatar(post.author, "thread-avatar")}
        <div>
          <div class="detail-heading">
            <strong>${escapeHtml(post.author.name || "Unknown")}</strong>
            ${post.author.verified ? '<span class="author-verified">Verified</span>' : ""}
          </div>
          <div class="detail-meta">
            ${post.author.username ? `@${escapeHtml(post.author.username)} · ` : ""}
            ${escapeHtml(formatDateTime(post.createdAt))}
          </div>
          <div class="detail-pill-row">
            ${post.bookmarked ? '<span class="detail-pill is-bookmarked">Bookmarked</span>' : ""}
            ${
              post.hasThread
                ? `<span class="detail-pill is-thread">${formatNumber(post.threadSize)} posts</span>`
                : ""
            }
            <span class="detail-pill">${escapeHtml(post.id)}</span>
          </div>
        </div>
      </div>
    </div>
    <div class="thread-text">${renderText(post.text)}</div>
  `;

  const mediaRoot = document.createElement("div");
  mediaRoot.className = "thread-media";
  fillMedia(mediaRoot, post.media, true);
  if (mediaRoot.childElementCount) {
    hero.append(mediaRoot);
  }

  const metrics = document.createElement("div");
  metrics.className = "metric-row";
  fillMetrics(metrics, post.metrics);
  hero.append(metrics);

  const fragment = document.createDocumentFragment();
  fragment.append(hero);

  if (post.links.length) {
    fragment.append(renderLinkSection(post.links));
  }
  if (post.references.length) {
    fragment.append(renderReferenceSection(post.references));
  }
  fragment.append(renderThreadSection(thread));

  detailBody.replaceChildren(fragment);
}

function renderLinkSection(links) {
  const section = document.createElement("section");
  section.className = "detail-links";
  section.innerHTML = `<h3 class="detail-section-title">Links</h3>`;
  for (const link of links) {
    const card = document.createElement("a");
    card.className = "link-card ghost-link";
    card.href = link.url;
    card.target = "_blank";
    card.rel = "noreferrer";
    card.innerHTML = `
      <div><strong>${escapeHtml(link.title || link.displayUrl)}</strong></div>
      <div class="detail-meta">${escapeHtml(link.displayUrl)}</div>
      ${
        link.description
          ? `<div class="detail-meta">${escapeHtml(link.description)}</div>`
          : ""
      }
    `;
    section.append(card);
  }
  return section;
}

function renderReferenceSection(references) {
  const section = document.createElement("section");
  section.className = "reference-list";
  section.innerHTML = `<h3 class="detail-section-title">References</h3>`;
  for (const reference of references) {
    const card = document.createElement("div");
    card.className = "reference-card";
    const archived = reference.isArchived && reference.target;
    card.innerHTML = `
      <div class="thread-line">
        <span class="ref-pill">${escapeHtml(reference.type)}</span>
        <span class="thread-meta">${escapeHtml(reference.targetId)}</span>
      </div>
      ${
        archived
          ? `<div class="thread-text">${renderText(reference.target.text)}</div>`
          : '<div class="detail-meta">Referenced post is not archived locally.</div>'
      }
    `;
    if (archived) {
      const action = document.createElement("button");
      action.className = "segment";
      action.type = "button";
      action.textContent = "Open archived post";
      action.addEventListener("click", () => {
        state.selectedId = reference.target.id;
        renderTimeline();
        selectPost(reference.target.id, true);
      });
      card.append(action);
    }
    section.append(card);
  }
  return section;
}

function renderThreadSection(thread) {
  const section = document.createElement("section");
  section.className = "thread-list";
  section.innerHTML = `<h3 class="detail-section-title">Conversation</h3>`;
  for (const item of thread.posts) {
    const node = document.createElement("article");
    node.className = `thread-item${item.isSelected ? " is-selected" : ""}`;
    node.innerHTML = `
      ${renderAvatar(item.author, "thread-avatar")}
      <div>
        <div class="thread-line">
          <strong>${escapeHtml(item.author.name || "Unknown")}</strong>
          ${item.author.username ? `<span class="thread-meta">@${escapeHtml(item.author.username)}</span>` : ""}
          <span class="thread-meta">${escapeHtml(formatDate(item.createdAt))}</span>
        </div>
        ${item.replyToPostId ? `<div class="thread-meta">Reply to ${escapeHtml(item.replyToPostId)}</div>` : ""}
        <div class="thread-text">${renderText(item.text)}</div>
      </div>
    `;
    const mediaRoot = document.createElement("div");
    mediaRoot.className = "thread-media";
    fillMedia(mediaRoot, item.media, false);
    if (mediaRoot.childElementCount) {
      node.lastElementChild.append(mediaRoot);
    }
    section.append(node);
  }
  if (thread.missingPostIds.length) {
    const missing = document.createElement("div");
    missing.className = "detail-meta";
    missing.textContent = `Missing: ${thread.missingPostIds.join(", ")}`;
    section.append(missing);
  }
  return section;
}

function fillMedia(root, media, autoplayVideo) {
  root.replaceChildren();
  for (const item of media) {
    const frame = document.createElement("div");
    frame.className = "media-frame";
    if (item.videoUrl) {
      const video = document.createElement("video");
      video.src = item.videoUrl;
      video.controls = true;
      video.preload = "metadata";
      video.poster = item.localUrl || item.remoteUrl || "";
      frame.append(video);
    } else if (item.localUrl || item.remoteUrl) {
      const image = document.createElement("img");
      image.loading = "lazy";
      image.src = item.localUrl || item.remoteUrl;
      image.alt = item.altText || "";
      frame.append(image);
    }
    if (frame.childElementCount) {
      root.append(frame);
    }
  }
}

function fillTags(root, post) {
  const tags = [];
  if (post.bookmarked) {
    tags.push('<span class="tag is-bookmarked">Bookmarked</span>');
  }
  if (post.hasThread) {
    tags.push(`<span class="tag is-thread">${formatNumber(post.threadSize)} posts</span>`);
  }
  if (post.replyToPostId) {
    tags.push(`<span class="tag">Reply to ${escapeHtml(post.replyToPostId)}</span>`);
  }
  root.innerHTML = tags.join("");
}

function fillMetrics(root, metrics) {
  root.innerHTML = [
    ["Likes", metrics.likeCount],
    ["Replies", metrics.replyCount],
    ["Reposts", metrics.retweetCount],
    ["Bookmarks", metrics.bookmarkCount],
  ]
    .map(
      ([label, value]) =>
        `<div class="metric"><strong>${formatNumber(value)}</strong><span>${label}</span></div>`,
    )
    .join("");
}

function renderFailure(message) {
  timelineList.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
  renderEmptyDetail(message);
}

function renderEmptyDetail(message) {
  detailTitle.textContent = "Select a post";
  detailOpen.classList.add("hidden");
  detailBody.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function renderText(text) {
  return escapeHtml(text).replace(urlPattern, (match) => {
    const label = truncateUrl(match);
    return `<a href="${escapeAttr(match)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`;
  });
}

function labelForFilter() {
  if (state.filter === "bookmarked") {
    return "Bookmarked posts";
  }
  if (state.filter === "threads") {
    return "Threaded posts";
  }
  return "Posts";
}

function formatNumber(value) {
  return new Intl.NumberFormat("en-US", { notation: value > 9999 ? "compact" : "standard" }).format(
    value,
  );
}

function formatDate(value) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function truncateUrl(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.host}${parsed.pathname}`.replace(/\/$/, "");
  } catch {
    return url;
  }
}

function avatarFallback(seed = "x") {
  const initial = escapeXml((seed || "X").trim().slice(0, 1).toUpperCase() || "X");
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">` +
    `<rect width="96" height="96" rx="12" fill="#111111"/>` +
    `<text x="48" y="58" text-anchor="middle" fill="#e7e9ea" ` +
    `font-family="Arial, sans-serif" font-size="40" font-weight="700">${initial}</text>` +
    `</svg>`;
  return `data:image/svg+xml;base64,${btoa(svg)}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}

function renderAvatar(author, className) {
  const fallback = avatarFallback(author.name);
  const src = author.avatarUrl || fallback;
  return `<img class="${className}" src="${escapeAttr(src)}" alt="${escapeAttr(
    author.name || "Avatar",
  )}" onerror="this.onerror=null;this.src='${escapeAttr(fallback)}'" />`;
}

function setAvatar(image, author) {
  const fallback = avatarFallback(author.name);
  image.src = author.avatarUrl || fallback;
  image.alt = author.name || "Avatar";
  image.onerror = () => {
    image.onerror = null;
    image.src = fallback;
  };
}

function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

const urlPattern = /https?:\/\/[^\s]+/g;

bootstrap().catch((error) => {
  console.error(error);
  renderFailure("Viewer failed to initialize.");
});
