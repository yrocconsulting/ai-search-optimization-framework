<?php
/**
 * Plugin Name: Claude SEO Bridge
 * Description: Exposes the SEO title, meta description, and focus keyword of Yoast SEO, Rank Math, and SEOPress through the WordPress REST API so they can be edited remotely with an application password. Only users who can edit a post can read or change its fields.
 * Version: 1.0.0
 * Requires at least: 5.6
 * Requires PHP: 7.2
 * License: GPL-2.0-or-later
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

const CLAUDE_SEO_BRIDGE_VERSION = '1.0.0';

function claude_seo_bridge_keys() {
	return array(
		'yoast'    => array( '_yoast_wpseo_title', '_yoast_wpseo_metadesc', '_yoast_wpseo_focuskw' ),
		'rankmath' => array( 'rank_math_title', 'rank_math_description', 'rank_math_focus_keyword' ),
		'seopress' => array( '_seopress_titles_title', '_seopress_titles_desc', '_seopress_analysis_target_kw' ),
	);
}

function claude_seo_bridge_active_plugins() {
	$active = array();
	if ( defined( 'WPSEO_VERSION' ) ) {
		$active[] = 'yoast';
	}
	if ( class_exists( 'RankMath' ) ) {
		$active[] = 'rankmath';
	}
	if ( defined( 'SEOPRESS_VERSION' ) ) {
		$active[] = 'seopress';
	}
	return $active;
}

function claude_seo_bridge_post_types() {
	return array_values( get_post_types( array( 'public' => true, 'show_in_rest' => true ) ) );
}

add_action(
	'init',
	function () {
		$keys = claude_seo_bridge_keys();
		foreach ( claude_seo_bridge_post_types() as $post_type ) {
			if ( 'attachment' === $post_type ) {
				continue;
			}
			// Meta only appears in REST responses for types that support custom fields.
			add_post_type_support( $post_type, 'custom-fields' );
			foreach ( claude_seo_bridge_active_plugins() as $plugin ) {
				foreach ( $keys[ $plugin ] as $meta_key ) {
					register_post_meta(
						$post_type,
						$meta_key,
						array(
							'type'              => 'string',
							'single'            => true,
							'show_in_rest'      => true,
							'sanitize_callback' => 'sanitize_text_field',
							'auth_callback'     => function ( $allowed, $meta_key, $post_id ) {
								return current_user_can( 'edit_post', $post_id );
							},
						)
					);
				}
			}
		}
	},
	99
);

add_action(
	'rest_api_init',
	function () {
		register_rest_route(
			'claude-seo-bridge/v1',
			'/status',
			array(
				'methods'             => 'GET',
				'permission_callback' => function () {
					return current_user_can( 'edit_posts' );
				},
				'callback'            => function () {
					return array(
						'version'     => CLAUDE_SEO_BRIDGE_VERSION,
						'seo_plugins' => claude_seo_bridge_active_plugins(),
						'post_types'  => claude_seo_bridge_post_types(),
					);
				},
			)
		);
	}
);
