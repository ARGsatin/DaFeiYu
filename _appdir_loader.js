// 临时：替换 QQ 应用目录里那个指向 QQChatExporter 的旧 loader。
// 它会把加载结果写到 D:\NapCat\probe.log，方便确认。
(async () => {
  try {
    const fs = await import('node:fs');
    const url = await import('node:url');
    const log = (s) => {
      try {
        fs.appendFileSync('D:\\NapCat\\probe.log', new Date().toISOString() + '  ' + s + '\n');
      } catch (e) {}
    };
    const main = process.env.NAPCAT_MAIN_PATH || 'D:\\NapCat\\napcat.mjs';
    log('appdir loader start, main=' + main);
    await import(url.pathToFileURL(main).href);
    log('import ok');
  } catch (e) {
    try {
      const fs2 = await import('node:fs');
      fs2.appendFileSync('D:\\NapCat\\probe.log',
        new Date().toISOString() + '  ERR ' + (e && e.stack ? e.stack : String(e)) + '\n');
    } catch (e2) {}
  }
})();
