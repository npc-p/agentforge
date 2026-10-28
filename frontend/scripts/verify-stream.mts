/**
 * 验证「流式读取中断」被正确识别为 stream_interrupted。
 *
 * 为什么需要它：断线重连依赖前端能区分「网络断了」和「业务失败」，而这条分支
 * 在正常网络下几乎不可复现。这里用一个会中途 error 的 ReadableStream 模拟
 * 后端进程被 kill 的场景，直接断言错误码。
 *
 * 运行（Node 22+ 原生支持 TS 类型擦除）：
 *   node --experimental-strip-types scripts/verify-stream.mts
 */
import { ApiError, streamChat } from '../src/lib/api.ts';

const encoder = new TextEncoder();

globalThis.fetch = async () =>
  new Response(
    new ReadableStream({
      start(controller) {
        // 先正常吐一个事件，模拟「已经渲染出一部分内容」
        controller.enqueue(encoder.encode('event: token\ndata: {"delta":"部分内容"}\n\n'));
        // 随后连接被掐断（等价于后端进程重启 / 网络抖动）
        setTimeout(() => controller.error(new Error('network reset')), 20);
      },
    }),
    { status: 200, headers: { 'content-type': 'text/event-stream' } },
  );

const events: string[] = [];
let caught: unknown;
try {
  await streamChat({ query: '测试断线' }, (type) => events.push(type));
} catch (err) {
  caught = err;
}

const isApiError = caught instanceof ApiError;
const code = isApiError ? (caught as ApiError).code : '(不是 ApiError)';
const passed = isApiError && code === 'stream_interrupted' && events.includes('token');

console.log('已收到事件      :', events.length ? events.join(', ') : '(无)');
console.log('抛出错误类型    :', (caught as Error)?.constructor?.name ?? '(未抛出)');
console.log('错误码          :', code);
console.log(passed ? 'PASS 断线被识别，且已接收内容保留' : 'FAIL');
process.exit(passed ? 0 : 1);
