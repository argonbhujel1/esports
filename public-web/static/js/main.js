// ESPORTS Worlds - Vanilla JS
document.addEventListener('DOMContentLoaded', () => {
  // Flash auto-hide
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => {
      el.style.transition = 'opacity 0.4s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 400);
    }, 4500);
  });

  // Mobile nav toggle (simple)
  const nav = document.querySelector('.nav-links');
  // Future: hamburger menu
});
