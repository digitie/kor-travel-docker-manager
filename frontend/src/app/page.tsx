import type { Metadata } from 'next';
import DashboardClient from '../components/DashboardClient';

export const metadata: Metadata = {
  // AppShell의 formatAdminTitle(APP_AREA, 홈 라벨)과 같은 "영역 - 페이지" 형식이다.
  title: 'Docker Manager - 대시보드',
  description: 'Docker 인프라 컨테이너 관리와 메트릭 운영 콘솔',
};

export default function Page() {
  return <DashboardClient />;
}
