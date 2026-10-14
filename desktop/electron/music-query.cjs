const normalize = (value) =>
  String(value || '')
    .normalize('NFKC')
    .replace(/[\s《》“”"'·・]/g, '')
    .toLowerCase();
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function parseQuery(value) {
  const query = String(value || '')
      .trim()
      .replace(/[。.!！?？]+$/, '')
      .slice(0, 160),
    quoted = query.match(/《([^》]+)》/),
    artistOnly = query.match(/^(.+?)(?:的歌曲|的歌)$/);
  if (artistOnly)
    return {
      query: artistOnly[1].trim(),
      title: '',
      artist: artistOnly[1].trim(),
      artistOnly: true,
    };
  if (quoted) {
    const artist = query
      .replace(quoted[0], '')
      .replace(/^(?:歌手|演唱|by|由)[:：\s]*/i, '')
      .trim();
    return { query: [quoted[1], artist].filter(Boolean).join(' '), title: quoted[1], artist };
  }
  const parts = query.split(/\s+/);
  if (parts.length > 1 && /[\u4e00-\u9fff]/.test(parts[0]))
    return { query, title: parts.shift(), artist: parts.join(' ') };
  return { query, title: query, artist: '' };
}
function textChunks(snapshot) {
  return snapshot.lines.flatMap((line) => {
    const groups = [];
    for (const word of line.words || []) {
      const last = groups.at(-1);
      if (last && word.x - last.x - last.width < Math.max(word.height, last.height) * 0.8) {
        last.text += ' ' + word.text;
        last.width = word.x + word.width - last.x;
      } else groups.push({ ...word });
    }
    return [line, ...groups];
  });
}
const interruption = (error) =>
  /user_interruption|desktop_locked|automation_paused|task_cancelled/.test(String(error));
module.exports = { normalize, parseQuery, textChunks, delay, interruption };
