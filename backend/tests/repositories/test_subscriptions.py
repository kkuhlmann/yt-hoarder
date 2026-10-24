from repositories import subscriptions


async def test_get_all_subscriptions(test_database):
    # testing with no limits
    response = await subscriptions.get_all_subscriptions()
    count_records = response['count_records']
    records = response['records']

    assert len(records) == 2
    assert count_records == 2

    # testing with a limit and paging
    page_1_response = await subscriptions.get_all_subscriptions(page=1, page_size=1)
    page_2_response = await subscriptions.get_all_subscriptions(page=2, page_size=1)

    assert len(page_1_response['records']) == 1
    assert len(page_2_response['records']) == 1
    assert page_1_response['count_records'] == 2
    assert page_2_response['count_records'] == 2
    assert page_1_response['records'][0]['id'] != page_2_response['records'][0]['id']


async def test_get_all_subscriptions_with_search(test_database):
    # Testing searching by channel name
    response = await subscriptions.get_all_subscriptions(page=1, page_size=10, search='Lesh')
    assert response['count_records'] == 1
    assert response['records'][0]['channel'] == 'Lesh'


async def test_get_subscription_by_id(test_database):
    subscription = await subscriptions.get_subscription_by_id(1)
    assert subscription is not None
    assert subscription.id == 1
    assert subscription.channel == 'Lesh'


async def test_update_subscription_toggles_enabled(test_database):
    """`enabled` must be in update_subscription's allowlist or the toggle silently no-ops."""
    updated = await subscriptions.update_subscription(1, {'enabled': False})
    assert updated.enabled is False

    reloaded = await subscriptions.get_subscription_by_id(1)
    assert reloaded.enabled is False

    updated = await subscriptions.update_subscription(1, {'enabled': True})
    assert updated.enabled is True


async def test_get_all_subscriptions_includes_disabled(test_database):
    """The table must keep showing disabled rows; only the cron read filters them out."""
    await subscriptions.update_subscription(1, {'enabled': False})

    response = await subscriptions.get_all_subscriptions()
    assert response['count_records'] == 2
    assert {r['id']: r['enabled'] for r in response['records']} == {1: False, 2: True}


async def test_sync_get_enabled_subscriptions_excludes_disabled(test_database):
    assert {s.id for s in subscriptions.sync_get_enabled_subscriptions()} == {1, 2}

    await subscriptions.update_subscription(1, {'enabled': False})

    assert {s.id for s in subscriptions.sync_get_enabled_subscriptions()} == {2}


async def test_sync_get_subscription_by_id_includes_disabled(test_database):
    """The on-demand check reads through this, and it must run on a disabled subscription."""
    from tasks.subscriptions import get_subscription_impl

    await subscriptions.update_subscription(1, {'enabled': False})

    sub = subscriptions.sync_get_subscription_by_id(1)
    assert sub is not None
    assert sub.enabled is False
    assert [s['id'] for s in get_subscription_impl(1)] == [1]


async def test_sync_get_subscription_by_id_missing(test_database):
    from tasks.subscriptions import get_subscription_impl

    assert subscriptions.sync_get_subscription_by_id(999) is None
    assert get_subscription_impl(999) == []


async def test_delete_subscription(test_database):
    # First verify it exists
    subscription = await subscriptions.get_subscription_by_id(1)
    assert subscription is not None

    # Delete it
    count = await subscriptions.delete_subscription(1)
    assert count == 1

    # Verify it's gone
    subscription = await subscriptions.get_subscription_by_id(1)
    assert subscription is None
